from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .audit import AuditReport
from .config import PRODUCT_SPECS, ProductSpec, resolve_products
from .exclusions import apply_known_exclusions
from .http import HttpClient, HttpSettings
from .models import DrawRecord, PrizeRecord, canonical_json, utc_now_iso
from .pipeline import Collector, CollectorOptions
from .sources import OfficialVietlottSource, SecondaryBatch, SecondaryResultSource
from .state import StateStore
from .storage import SqliteDatasetStore

LOGGER = logging.getLogger(__name__)
FAST_PRODUCTS = ("keno", "bingo18")
ACTIVE_SLOW_PRODUCTS = ("mega645", "power655", "lotto535", "max3d", "max3dpro")


def run_update(
    *,
    products: list[str],
    output_dir: Path,
    reconcile_pages: int,
    request_delay: float,
    jitter: float,
    retries: int,
    timeout: float,
    contact: str,
) -> dict[str, object]:
    specs = resolve_products(products)
    store = SqliteDatasetStore(output_dir)
    store.import_csv_if_empty()
    before_draws, before_prizes = store.counts()
    source_errors = 0
    reconciliation: dict[str, object] = {}
    fallback_reports: dict[str, object] = {}

    settings = HttpSettings(
        timeout_seconds=timeout,
        request_delay_seconds=request_delay,
        jitter_seconds=jitter,
        retry_total=retries,
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
            "Gecko/20100101 Firefox/128.0 "
            f"NhanAZ-vietlott-data-research/0.2 contact/{contact}"
        ),
    )
    try:
        with HttpClient(settings) as client:
            source = OfficialVietlottSource(client)
            secondary_source = SecondaryResultSource(client)
            state_store = StateStore(output_dir / ".collector-state.json")
            summaries = []
            for include_prizes, selected in (
                (False, [spec for spec in specs if spec.slug in FAST_PRODUCTS]),
                (True, [spec for spec in specs if spec.slug not in FAST_PRODUCTS]),
            ):
                if not selected:
                    continue
                collector = Collector(
                    source,
                    store,
                    state_store,
                    CollectorOptions(
                        backfill=False,
                        include_prizes=include_prizes,
                        max_pages=None,
                    ),
                )
                summaries.extend(collector.collect(selected))
            failed_products = {
                summary.product for summary in summaries if summary.errors > 0
            }

            for spec in specs:
                if spec.slug not in failed_products:
                    continue
                try:
                    batch = secondary_source.fetch(spec)
                    fallback_reports[spec.slug] = _store_secondary_batch(
                        store,
                        batch,
                    )
                except Exception as exc:
                    source_errors += 1
                    fallback_reports[spec.slug] = {"error": str(exc)}
                    LOGGER.exception("Secondary collection failed for %s", spec.slug)

            for spec in specs:
                if spec.slug in failed_products:
                    if "error" not in fallback_reports.get(spec.slug, {}):
                        reconciliation[spec.slug] = {
                            "skipped": "official source unavailable",
                            "fallback_source": fallback_reports[spec.slug],
                        }
                    continue
                try:
                    reconciliation[spec.slug] = _reconcile_recent(
                        source,
                        store,
                        spec,
                        reconcile_pages,
                    )
                except Exception as exc:
                    source_errors += 1
                    reconciliation[spec.slug] = {"error": str(exc)}
                    LOGGER.exception("Recent reconciliation failed for %s", spec.slug)

        fast = [spec.slug for spec in specs if spec.slug in FAST_PRODUCTS]
        if fast:
            store.mark_rules_available(fast)
        exclusions = apply_known_exclusions(store)
        store.export_csv()
        after_draws, after_prizes = store.counts()
        audit = AuditReport(**store.audit_counts())
        report = {
            "updated_at": utc_now_iso(),
            "products": [spec.slug for spec in specs],
            "draw_rows_before": before_draws,
            "draw_rows_after": after_draws,
            "new_draw_rows": after_draws - before_draws,
            "prize_rows_before": before_prizes,
            "prize_rows_after": after_prizes,
            "new_prize_rows": after_prizes - before_prizes,
            "source_errors": source_errors,
            "official_source_errors": sum(summary.errors for summary in summaries),
            "summaries": [
                {
                    "product": summary.product,
                    "pages_fetched": summary.pages_fetched,
                    "draws_seen": summary.draws_seen,
                    "draws_written": summary.draws_written,
                    "prizes_written": summary.prizes_written,
                    "errors": summary.errors,
                }
                for summary in summaries
            ],
            "secondary_fallback": fallback_reports,
            "recent_reconciliation": reconciliation,
            "known_exclusions": exclusions,
            "audit": audit.as_dict(),
        }
        report_path = output_dir / "incremental-update-report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return report
    finally:
        store.close()


def _store_secondary_batch(
    store: SqliteDatasetStore,
    batch: SecondaryBatch,
) -> dict[str, object]:
    if not batch.draws:
        raise RuntimeError("Secondary source returned no draw records")

    existing_ids: dict[str, set[str]] = {}
    incomplete: dict[tuple[str, str], DrawRecord] = {}
    for product in {record.product for record in batch.draws}:
        existing_ids[product] = store.existing_draw_ids(product)
        incomplete.update(
            {
                record.key: record
                for record in store.incomplete_draw_records(product)
            }
        )

    prizes_by_draw: dict[tuple[str, str], list[PrizeRecord]] = {}
    for prize in batch.prizes:
        prizes_by_draw.setdefault((prize.product, prize.draw_id), []).append(prize)

    draw_updates: list[DrawRecord] = []
    prize_updates: list[PrizeRecord] = []
    inserted = 0
    prize_supplements = 0
    for record in batch.draws:
        draw_prizes = prizes_by_draw.get(record.key, [])
        if record.draw_id not in existing_ids[record.product]:
            draw_updates.append(record)
            prize_updates.extend(draw_prizes)
            inserted += 1
            continue

        old = incomplete.get(record.key)
        if old is None or not draw_prizes:
            continue
        if old.draw_date != record.draw_date or canonical_json(old.result) != canonical_json(
            record.result
        ):
            raise RuntimeError(
                f"Secondary result conflicts with stored draw {record.product} "
                f"{record.draw_id}"
            )
        old.prize_status = "secondary_complete"
        old.attributes["secondary_prize_source_url"] = batch.source_url
        draw_updates.append(old)
        prize_updates.extend(draw_prizes)
        prize_supplements += 1

    store.upsert(draw_updates, prize_updates)
    return {
        "source_url": batch.source_url,
        "draws_seen": len(batch.draws),
        "prizes_seen": len(batch.prizes),
        "draws_inserted": inserted,
        "prize_supplements": prize_supplements,
    }


def _reconcile_recent(
    source: OfficialVietlottSource,
    store: SqliteDatasetStore,
    spec: ProductSpec,
    pages: int,
) -> dict[str, object]:
    if pages <= 0:
        return {"checked": 0, "inserted": 0, "changed": 0}
    context = source.bootstrap(spec)
    records = []
    for page_index in range(pages):
        page = source.fetch_page(spec, page_index, context)
        records.extend(page)
        if len(page) < spec.page_size:
            break
    return store.reconcile_official_draws(records)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vietlott-auto-update",
        description="Incrementally update the repository dataset from official Vietlott pages.",
    )
    parser.add_argument(
        "--products",
        nargs="+",
        default=list(ACTIVE_SLOW_PRODUCTS),
        choices=[*PRODUCT_SPECS, "all"],
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    parser.add_argument("--reconcile-pages", type=int, default=2)
    parser.add_argument("--request-delay", type=float, default=0.8)
    parser.add_argument("--jitter", type=float, default=0.25)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--contact", default="60387689+NhanAZ@users.noreply.github.com")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING"), default="INFO")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    report = run_update(
        products=args.products,
        output_dir=args.output_dir,
        reconcile_pages=max(0, args.reconcile_pages),
        request_delay=max(0.0, args.request_delay),
        jitter=max(0.0, args.jitter),
        retries=max(0, args.retries),
        timeout=max(1.0, args.timeout),
        contact=args.contact,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.strict and int(report["source_errors"]) > 0:
        sys.exit(2)
