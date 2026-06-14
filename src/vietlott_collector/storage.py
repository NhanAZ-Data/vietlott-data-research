from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import sqlite3
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd

from .models import DrawRecord, PrizeRecord

LOGGER = logging.getLogger(__name__)
OutputFormat = Literal["csv", "parquet"]

DRAW_COLUMNS = [
    "product",
    "draw_id",
    "draw_date",
    "draw_status",
    "result_json",
    "attributes_json",
    "official_pdf_urls_json",
    "source_url",
    "prize_status",
    "validation_status",
    "validation_warnings_json",
    "fetched_at",
]

PRIZE_COLUMNS = [
    "product",
    "draw_id",
    "game_variant",
    "prize_tier",
    "winning_rule",
    "winner_count",
    "prize_value_vnd",
    "details_json",
    "source_url",
    "fetched_at",
]


class DatasetStore:
    def __init__(self, output_dir: Path, output_format: OutputFormat) -> None:
        self.output_dir = output_dir
        self.output_format = output_format
        self.draws_path = output_dir / f"draws.{output_format}"
        self.prizes_path = output_dir / f"prizes.{output_format}"

    def load_draws(self) -> pd.DataFrame:
        return self._read(self.draws_path, DRAW_COLUMNS)

    def load_prizes(self) -> pd.DataFrame:
        return self._read(self.prizes_path, PRIZE_COLUMNS)

    def existing_draw_ids(self, product: str) -> set[str]:
        draws = self.load_draws()
        if draws.empty:
            return set()
        selected = draws.loc[draws["product"] == product, "draw_id"]
        return set(selected.astype(str))

    def incomplete_prize_ids(self, product: str) -> set[str]:
        draws = self.load_draws()
        if draws.empty:
            return set()
        terminal = {"complete", "rules_available", "empty", "not_applicable"}
        mask = (draws["product"] == product) & (~draws["prize_status"].isin(terminal))
        return set(draws.loc[mask, "draw_id"].astype(str))

    def upsert(
        self,
        draws: Iterable[DrawRecord],
        prizes: Iterable[PrizeRecord],
    ) -> tuple[int, int]:
        new_draws = pd.DataFrame([record.to_row() for record in draws], columns=DRAW_COLUMNS)
        new_prizes = pd.DataFrame([record.to_row() for record in prizes], columns=PRIZE_COLUMNS)

        draw_frame = self._merge(
            self.load_draws(),
            new_draws,
            subset=["product", "draw_id"],
            sort_by=["draw_date", "product", "draw_id"],
        )
        prize_frame = self._merge(
            self.load_prizes(),
            new_prizes,
            subset=[
                "product",
                "draw_id",
                "game_variant",
                "prize_tier",
                "winning_rule",
                "prize_value_vnd",
            ],
            sort_by=["product", "draw_id", "game_variant", "prize_tier"],
        )

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._write_atomic(draw_frame, self.draws_path)
        self._write_atomic(prize_frame, self.prizes_path)
        return len(draw_frame), len(prize_frame)

    @staticmethod
    def _merge(
        old: pd.DataFrame,
        new: pd.DataFrame,
        *,
        subset: list[str],
        sort_by: list[str],
    ) -> pd.DataFrame:
        if new.empty:
            return old
        columns = list(dict.fromkeys([*old.columns, *new.columns]))
        frames = [frame.dropna(axis=1, how="all") for frame in (old, new) if not frame.empty]
        combined = pd.concat(frames, ignore_index=True).reindex(columns=columns)
        combined = combined.drop_duplicates(subset=subset, keep="last")
        return combined.sort_values(sort_by, kind="stable").reset_index(drop=True)

    def _read(self, path: Path, columns: list[str]) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=columns)
        if self.output_format == "csv":
            frame = pd.read_csv(path, dtype={"product": "string", "draw_id": "string"})
        else:
            frame = pd.read_parquet(path)
        for column in columns:
            if column not in frame.columns:
                frame[column] = "confirmed" if column == "draw_status" else None
        if "draw_status" in frame.columns:
            frame["draw_status"] = frame["draw_status"].fillna("confirmed")
        return frame[columns]

    def _write_atomic(self, frame: pd.DataFrame, path: Path) -> None:
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        if self.output_format == "csv":
            frame.to_csv(temp_path, index=False, encoding="utf-8")
        else:
            frame.to_parquet(temp_path, index=False, engine="pyarrow")
        os.replace(temp_path, path)
        LOGGER.debug("Wrote %d rows to %s", len(frame), path)


class SqliteDatasetStore:
    """Transactional working store for large, resumable backfills."""

    def __init__(self, output_dir: Path, database_name: str = "vietlott.sqlite3") -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = output_dir / database_name
        self.connection = sqlite3.connect(self.database_path, timeout=60)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        draw_columns = ",\n".join(f"{column} TEXT" for column in DRAW_COLUMNS)
        prize_columns = ",\n".join(
            f"{column} {'INTEGER' if column in {'winner_count', 'prize_value_vnd'} else 'TEXT'}"
            for column in PRIZE_COLUMNS
        )
        self.connection.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS draws (
                {draw_columns},
                PRIMARY KEY (product, draw_id)
            );
            CREATE TABLE IF NOT EXISTS prizes (
                {prize_columns},
                prize_key TEXT NOT NULL,
                PRIMARY KEY (product, draw_id, prize_key)
            );
            CREATE INDEX IF NOT EXISTS idx_draws_product_status
                ON draws(product, prize_status);
            CREATE INDEX IF NOT EXISTS idx_prizes_product_draw
                ON prizes(product, draw_id);
            """
        )
        existing_columns = {
            str(row[1]) for row in self.connection.execute("PRAGMA table_info(draws)")
        }
        if "draw_status" not in existing_columns:
            self.connection.execute(
                "ALTER TABLE draws ADD COLUMN draw_status TEXT DEFAULT 'confirmed'"
            )
        self.connection.execute(
            "UPDATE draws SET draw_status = 'confirmed' "
            "WHERE draw_status IS NULL OR TRIM(draw_status) = ''"
        )
        self.connection.commit()

    def import_csv_if_empty(self) -> tuple[int, int]:
        draw_count, prize_count = self.counts()
        if draw_count == 0:
            draw_count = self._import_csv(self.output_dir / "draws.csv", "draws", DRAW_COLUMNS)
        if prize_count == 0:
            prize_count = self._import_csv(self.output_dir / "prizes.csv", "prizes", PRIZE_COLUMNS)
        return draw_count, prize_count

    def _import_csv(self, path: Path, table: str, columns: list[str]) -> int:
        if not path.exists():
            return 0
        if table == "draws":
            placeholders = ",".join("?" for _ in columns)
            sql = f"INSERT OR REPLACE INTO draws ({','.join(columns)}) VALUES ({placeholders})"
        else:
            all_columns = [*columns, "prize_key"]
            placeholders = ",".join("?" for _ in all_columns)
            sql = f"INSERT OR REPLACE INTO prizes ({','.join(all_columns)}) VALUES ({placeholders})"
        imported = 0
        with self.connection, path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            batch: list[tuple[object, ...]] = []
            for row in reader:
                if table == "draws":
                    values = tuple(
                        row.get(column)
                        or ("confirmed" if column == "draw_status" else None)
                        for column in columns
                    )
                else:
                    values = (
                        *(row.get(column) or None for column in columns),
                        _prize_key(row),
                    )
                batch.append(values)
                if len(batch) >= 10_000:
                    self.connection.executemany(sql, batch)
                    imported += len(batch)
                    batch.clear()
            if batch:
                self.connection.executemany(sql, batch)
                imported += len(batch)
        LOGGER.info("Imported %d rows from %s", imported, path)
        return imported

    def load_draws(self) -> pd.DataFrame:
        return pd.read_sql_query(
            f"SELECT {','.join(DRAW_COLUMNS)} FROM draws",
            self.connection,
            dtype={"product": "string", "draw_id": "string"},
        )

    def load_prizes(self) -> pd.DataFrame:
        return pd.read_sql_query(
            f"SELECT {','.join(PRIZE_COLUMNS)} FROM prizes",
            self.connection,
            dtype={"product": "string", "draw_id": "string"},
        )

    def existing_draw_ids(self, product: str) -> set[str]:
        cursor = self.connection.execute(
            "SELECT draw_id FROM draws WHERE product = ?",
            (product,),
        )
        return {str(row[0]) for row in cursor}

    def incomplete_draw_records(self, product: str, limit: int = 500) -> list[DrawRecord]:
        cursor = self.connection.execute(
            f"""
            SELECT {",".join(DRAW_COLUMNS)}
            FROM draws
            WHERE product = ?
              AND prize_status NOT IN (
                  'complete', 'rules_available', 'empty', 'not_applicable'
              )
            ORDER BY draw_id
            LIMIT ?
            """,
            (product, limit),
        )
        records: list[DrawRecord] = []
        for values in cursor:
            row = dict(zip(DRAW_COLUMNS, values, strict=True))
            records.append(
                DrawRecord(
                    product=str(row["product"]),
                    draw_id=str(row["draw_id"]),
                    draw_date=date.fromisoformat(str(row["draw_date"])),
                    draw_status=str(row["draw_status"]),
                    result=_json_object(row["result_json"]),
                    attributes=_json_object(row["attributes_json"]),
                    official_pdf_urls=_json_list(row["official_pdf_urls_json"]),
                    source_url=str(row["source_url"]),
                    prize_status=str(row["prize_status"]),
                    validation_status=str(row["validation_status"]),
                    validation_warnings=_json_list(row["validation_warnings_json"]),
                    fetched_at=str(row["fetched_at"]),
                )
            )
        return records

    def incomplete_prize_count(self, product: str) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM draws "
                "WHERE product = ? "
                "AND prize_status NOT IN "
                "('complete', 'rules_available', 'empty', 'not_applicable')",
                (product,),
            ).fetchone()[0]
        )

    def missing_numeric_draw_ids(
        self,
        product: str,
        oldest_id: int,
        newest_id: int,
    ) -> list[int]:
        existing = {
            int(row[0])
            for row in self.connection.execute(
                "SELECT draw_id FROM draws WHERE product = ? AND CAST(draw_id AS INTEGER) BETWEEN ? AND ?",
                (product, oldest_id, newest_id),
            )
            if str(row[0]).isdigit()
        }
        return [draw_id for draw_id in range(oldest_id, newest_id + 1) if draw_id not in existing]

    def incomplete_prize_ids(self, product: str) -> set[str]:
        cursor = self.connection.execute(
            "SELECT draw_id FROM draws WHERE product = ? "
            "AND prize_status NOT IN "
            "('complete', 'rules_available', 'empty', 'not_applicable')",
            (product,),
        )
        return {str(row[0]) for row in cursor}

    def insert_missing_draws(self, draws: Iterable[DrawRecord]) -> int:
        rows = [record.to_row() for record in draws]
        if not rows:
            return 0
        placeholders = ",".join("?" for _ in DRAW_COLUMNS)
        before = self.connection.total_changes
        with self.connection:
            self.connection.executemany(
                f"INSERT OR IGNORE INTO draws ({','.join(DRAW_COLUMNS)}) VALUES ({placeholders})",
                [tuple(row[column] for column in DRAW_COLUMNS) for row in rows],
            )
        return self.connection.total_changes - before

    def reconcile_official_draws(
        self,
        draws: Iterable[DrawRecord],
    ) -> dict[str, object]:
        stats: dict[str, object] = {
            "checked": 0,
            "inserted": 0,
            "changed": 0,
            "date_mismatches": 0,
            "result_mismatches": 0,
            "examples": [],
        }
        examples: list[dict[str, object]] = stats["examples"]  # type: ignore[assignment]
        with self.connection:
            for record in draws:
                stats["checked"] = int(stats["checked"]) + 1
                row = record.to_row()
                existing = self.connection.execute(
                    f"SELECT {','.join(DRAW_COLUMNS)} FROM draws WHERE product = ? AND draw_id = ?",
                    (record.product, record.draw_id),
                ).fetchone()
                if existing is None:
                    attributes = dict(record.attributes)
                    attributes["data_source"] = "official_vietlott"
                    row["attributes_json"] = json.dumps(
                        attributes,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    placeholders = ",".join("?" for _ in DRAW_COLUMNS)
                    self.connection.execute(
                        f"INSERT INTO draws ({','.join(DRAW_COLUMNS)}) VALUES ({placeholders})",
                        tuple(row[column] for column in DRAW_COLUMNS),
                    )
                    stats["inserted"] = int(stats["inserted"]) + 1
                    continue

                old = dict(zip(DRAW_COLUMNS, existing, strict=True))
                date_changed = str(old["draw_date"]) != str(row["draw_date"])
                result_changed = str(old["result_json"]) != str(row["result_json"])
                if date_changed:
                    stats["date_mismatches"] = int(stats["date_mismatches"]) + 1
                if result_changed:
                    stats["result_mismatches"] = int(stats["result_mismatches"]) + 1
                if (date_changed or result_changed) and len(examples) < 50:
                    examples.append(
                        {
                            "product": record.product,
                            "draw_id": record.draw_id,
                            "old_date": old["draw_date"],
                            "new_date": row["draw_date"],
                            "old_result_json": old["result_json"],
                            "new_result_json": row["result_json"],
                        }
                    )
                if date_changed or result_changed:
                    stats["changed"] = int(stats["changed"]) + 1

                attributes = _json_object(old["attributes_json"])
                provenance_changed = (
                    "secondary_source_url" in attributes
                    or "upstream_claimed_source" in attributes
                    or attributes.get("data_source") != "official_vietlott"
                )
                if not date_changed and not result_changed and not provenance_changed:
                    continue
                attributes.pop("secondary_source_url", None)
                attributes.pop("upstream_claimed_source", None)
                attributes.pop("official_verification_status", None)
                attributes.pop("secondary_prize_source_url", None)
                attributes["data_source"] = "official_vietlott"
                attributes["official_list_verified_at"] = row["fetched_at"]
                self.connection.execute(
                    """
                    UPDATE draws
                    SET draw_date = ?,
                        result_json = ?,
                        attributes_json = ?,
                        source_url = ?,
                        validation_status = ?,
                        validation_warnings_json = ?,
                        fetched_at = ?
                    WHERE product = ? AND draw_id = ?
                    """,
                    (
                        row["draw_date"],
                        row["result_json"],
                        json.dumps(
                            attributes,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        row["source_url"],
                        row["validation_status"],
                        row["validation_warnings_json"],
                        row["fetched_at"],
                        record.product,
                        record.draw_id,
                    ),
                )
        return stats

    def upsert(
        self,
        draws: Iterable[DrawRecord],
        prizes: Iterable[PrizeRecord],
    ) -> tuple[int, int]:
        draw_rows = [record.to_row() for record in draws]
        prize_rows = [record.to_row() for record in prizes]
        if draw_rows:
            placeholders = ",".join("?" for _ in DRAW_COLUMNS)
            updates = ",".join(
                f"{column}=excluded.{column}"
                for column in DRAW_COLUMNS
                if column not in {"product", "draw_id"}
            )
            with self.connection:
                self.connection.executemany(
                    f"INSERT INTO draws ({','.join(DRAW_COLUMNS)}) VALUES ({placeholders}) "
                    f"ON CONFLICT(product, draw_id) DO UPDATE SET {updates}",
                    [tuple(row[column] for column in DRAW_COLUMNS) for row in draw_rows],
                )
        if prize_rows:
            draw_keys = sorted({(row["product"], row["draw_id"]) for row in prize_rows})
            all_columns = [*PRIZE_COLUMNS, "prize_key"]
            placeholders = ",".join("?" for _ in all_columns)
            with self.connection:
                self.connection.executemany(
                    "DELETE FROM prizes WHERE product = ? AND draw_id = ?",
                    draw_keys,
                )
                self.connection.executemany(
                    f"INSERT OR REPLACE INTO prizes ({','.join(all_columns)}) VALUES ({placeholders})",
                    [
                        (
                            *(row[column] for column in PRIZE_COLUMNS),
                            _prize_key(row),
                        )
                        for row in prize_rows
                    ],
                )
        return self.counts()

    def counts(self) -> tuple[int, int]:
        draws = int(self.connection.execute("SELECT COUNT(*) FROM draws").fetchone()[0])
        prizes = int(self.connection.execute("SELECT COUNT(*) FROM prizes").fetchone()[0])
        return draws, prizes

    def audit_counts(self) -> dict[str, object]:
        required = ["product", "draw_id", "draw_date", "result_json", "source_url"]
        missing = {
            column: int(
                self.connection.execute(
                    f"SELECT COUNT(*) FROM draws WHERE {column} IS NULL OR TRIM({column}) = ''"
                ).fetchone()[0]
            )
            for column in required
        }
        draw_rows, prize_rows = self.counts()
        warning_draws = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM draws WHERE validation_status = 'warning'"
            ).fetchone()[0]
        )
        incomplete = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM draws "
                "WHERE prize_status NOT IN "
                "('complete', 'rules_available', 'empty', 'not_applicable')"
            ).fetchone()[0]
        )
        return {
            "draw_rows": draw_rows,
            "prize_rows": prize_rows,
            "duplicate_draws": 0,
            "duplicate_prizes": 0,
            "missing_draw_fields": missing,
            "warning_draws": warning_draws,
            "incomplete_prize_draws": incomplete,
        }

    def mark_rules_available(self, products: Iterable[str]) -> None:
        values = [(product,) for product in products]
        with self.connection:
            self.connection.executemany(
                "UPDATE draws SET prize_status = 'rules_available' "
                "WHERE product = ? "
                "AND prize_status NOT IN ('complete', 'not_applicable')",
                values,
            )

    def export_csv(self) -> tuple[Path, Path, Path]:
        draws_path = self.output_dir / "draws.csv"
        prizes_path = self.output_dir / "prizes.csv"
        rules_path = self.output_dir / "prize_rules.csv"
        self._export_table("draws", DRAW_COLUMNS, draws_path, "draw_date, product, draw_id")
        self._export_table(
            "prizes",
            PRIZE_COLUMNS,
            prizes_path,
            "product, draw_id, game_variant, prize_tier",
        )
        self._export_prize_rules(rules_path)
        return draws_path, prizes_path, rules_path

    def _export_table(
        self,
        table: str,
        columns: list[str],
        path: Path,
        order_by: str,
    ) -> None:
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        cursor = self.connection.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY {order_by}")
        with temp_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            while rows := cursor.fetchmany(10_000):
                writer.writerows(rows)
        os.replace(temp_path, path)

    def _export_prize_rules(self, path: Path) -> None:
        columns = [
            "product",
            "game_variant",
            "prize_tier",
            "winning_rule",
            "prize_value_vnd",
            "details_json",
            "source_url",
        ]
        placeholders = ",".join(columns)
        cursor = self.connection.execute(
            f"""
            SELECT {placeholders}
            FROM prizes
            WHERE product IN ('keno', 'bingo18')
            GROUP BY product, game_variant, prize_tier, winning_rule, prize_value_vnd
            ORDER BY product, game_variant, prize_tier, winning_rule, prize_value_vnd
            """
        )
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        with temp_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(cursor)
        os.replace(temp_path, path)


def _prize_key(row: dict[str, object]) -> str:
    identity = "\x1f".join(
        str(row.get(column) or "")
        for column in (
            "game_variant",
            "prize_tier",
            "winning_rule",
            "prize_value_vnd",
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _json_object(value: object) -> dict:
    if not value:
        return {}
    parsed = json.loads(str(value))
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: object) -> list:
    if not value:
        return []
    parsed = json.loads(str(value))
    return parsed if isinstance(parsed, list) else []
