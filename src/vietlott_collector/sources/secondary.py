from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup, Tag

from ..config import ProductSpec
from ..http import HttpClient
from ..models import DrawRecord, PrizeRecord
from ..validation import validate_draw

BASE_URL = "https://xosominhngoc.net.vn"
PRODUCT_PATHS = {
    "mega645": "/kqxs-mega-645",
    "power655": "/kqxs-power-655",
    "lotto535": "/kqxs-lotto-535",
    "max3d": "/kqxs-max3d",
    "max3dpro": "/kqxs-max3d-pro",
    "keno": "/kqxs-keno",
    "bingo18": "/kqxs-bingo18",
}
BLOCK_SELECTORS = {
    "mega645": "article.xsmega",
    "power655": "div.xspower",
    "lotto535": "article.xslotto535",
    "max3d": "article.xsmax3d",
    "max3dpro": "article.xsmax3dpro",
    "keno": "article.xskeno",
    "bingo18": "article.xsbingo18",
}


@dataclass(slots=True)
class SecondaryBatch:
    draws: list[DrawRecord]
    prizes: list[PrizeRecord]
    source_url: str


class SecondaryResultSource:
    """Read recent public results when Vietlott blocks the GitHub runner."""

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def fetch(self, spec: ProductSpec) -> SecondaryBatch:
        path = PRODUCT_PATHS.get(spec.slug)
        if path is None:
            raise ValueError(f"No secondary source is configured for {spec.slug}")
        source_url = f"{BASE_URL}{path}"
        html = self.client.get_text(source_url)
        return parse_secondary_page(spec, html, source_url=source_url)


def parse_secondary_page(
    spec: ProductSpec,
    html: str,
    *,
    source_url: str,
) -> SecondaryBatch:
    soup = BeautifulSoup(html, "html.parser")
    blocks = soup.select(BLOCK_SELECTORS[spec.slug])
    if not blocks:
        raise RuntimeError(
            f"The secondary page contains no recognized {spec.slug} result blocks"
        )

    draws: list[DrawRecord] = []
    prizes: list[PrizeRecord] = []
    for block in blocks:
        record = _parse_draw(spec, block, source_url)
        warnings = validate_draw(record, spec)
        if warnings:
            raise RuntimeError(
                f"Secondary {spec.slug} draw {record.draw_id} failed validation: "
                + "; ".join(warnings)
            )
        draw_prizes = _parse_prizes(spec, block, record)
        if spec.slug in {"keno", "bingo18"}:
            record.prize_status = "rules_available"
        elif draw_prizes:
            record.prize_status = "secondary_complete"
        draws.append(record)
        prizes.extend(draw_prizes)

    return SecondaryBatch(draws=draws, prizes=prizes, source_url=source_url)


def _parse_draw(spec: ProductSpec, block: Tag, source_url: str) -> DrawRecord:
    draw_id = _draw_id(spec, block)
    draw_date = _draw_date(block)
    values = [text for span in block.select("span.kq") if (text := span.get_text(strip=True))]
    attributes: dict[str, Any] = {
        "data_source": "xosominhngoc_net_vn",
        "official_verification_status": "pending",
        "secondary_source_url": source_url,
        "upstream_claimed_source": "Vietlott",
    }
    time_match = re.search(r"\b(\d{1,2}:\d{2})\b", _text(block.select_one(".ngay")))
    if time_match:
        attributes["draw_time"] = time_match.group(1)

    if spec.slug in {"mega645", "power655", "lotto535"}:
        numbers = [int(value) for value in values]
        result: dict[str, Any] = {"numbers": numbers[: spec.main_count]}
        if spec.special_count:
            result["special_numbers"] = numbers[
                spec.main_count : spec.main_count + spec.special_count
            ]
    elif spec.slug in {"max3d", "max3dpro"}:
        tier_values = _three_digit_tiers(block)
        result = {"tiers": tier_values}
    elif spec.slug == "keno":
        numbers = [int(value) for value in values]
        even = sum(number % 2 == 0 for number in numbers)
        small = sum(number <= 40 for number in numbers)
        attributes["odd_even"] = {"even": even, "odd": len(numbers) - even}
        attributes["big_small"] = {"big": len(numbers) - small, "small": small}
        result = {"numbers": numbers}
    elif spec.slug == "bingo18":
        digits = [int(value) for value in values]
        attributes["total"] = sum(digits)
        attributes["has_duplicate"] = len(set(digits)) < len(digits)
        result = {"digits": digits}
    else:
        raise ValueError(f"Unsupported secondary product: {spec.slug}")

    return DrawRecord(
        product=spec.slug,
        draw_id=draw_id,
        draw_date=draw_date,
        result=result,
        attributes=attributes,
        source_url=source_url,
    )


def _three_digit_tiers(block: Tag) -> dict[str, list[str]]:
    if "xsmax3d" in block.get("class", []):
        cells = block.select("td.max3d_number")
    else:
        cells = block.select("td.kqnum")[:4]
    values = [
        [span.get_text(strip=True).zfill(3) for span in cell.select("span.kq")]
        for cell in cells[:4]
    ]
    if len(values) != 4:
        raise RuntimeError("Secondary three-digit result does not contain four tiers")
    return dict(zip(("special", "first", "second", "third"), values, strict=True))


def _parse_prizes(
    spec: ProductSpec,
    block: Tag,
    record: DrawRecord,
) -> list[PrizeRecord]:
    if spec.slug in {"mega645", "power655", "lotto535"}:
        return _parse_matrix_prizes(spec, block, record)
    if spec.slug == "max3d":
        return _parse_max3d_prizes(block, record)
    if spec.slug == "max3dpro":
        return _parse_max3dpro_prizes(block, record)
    return []


def _parse_matrix_prizes(
    spec: ProductSpec,
    block: Tag,
    record: DrawRecord,
) -> list[PrizeRecord]:
    variant = {
        "mega645": "Giá trị Jackpot",
        "power655": "Giá trị Jackpot 2",
        "lotto535": "Giải Độc Đắc",
    }[spec.slug]
    prizes: list[PrizeRecord] = []
    for index, row in enumerate(block.select("table tbody tr")):
        cells = [_text(cell) for cell in row.find_all(["th", "td"], recursive=False)]
        if len(cells) != 4:
            continue
        tier, rule, winners, value = cells
        prizes.append(
            _prize(
                record,
                variant=variant,
                tier=tier,
                rule=rule,
                winners=_integer(winners),
                value=_vnd(value),
                details={"columns": cells, "row_index": index},
            )
        )
    return prizes


def _parse_max3d_prizes(block: Tag, record: DrawRecord) -> list[PrizeRecord]:
    prizes: list[PrizeRecord] = []
    tier_numbers = record.result["tiers"]
    for index, row in enumerate(block.select("table.table_max3d tbody tr")):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) != 3:
            continue
        middle_numbers = [span.get_text(strip=True).zfill(3) for span in cells[1].select("span.kq")]
        for cell_index, variant in ((0, "Max 3D"), (2, "Max 3D+")):
            tier = _first_line(cells[cell_index])
            if not tier:
                continue
            amount = _compact_vnd(_text(cells[cell_index].select_one(".giaiMax3d")))
            winners = _integer(_text(cells[cell_index].select_one(".max3d_sl")))
            rule = _text(cells[1].select_one(".noteGiai"))
            if not rule and middle_numbers:
                rule = " ".join(middle_numbers)
            prizes.append(
                _prize(
                    record,
                    variant=variant,
                    tier=tier,
                    rule=rule or None,
                    winners=winners,
                    value=amount,
                    details={
                        "row_index": index,
                        "tier_numbers": tier_numbers,
                        "raw_row": _text(row),
                    },
                )
            )
    return prizes


def _parse_max3dpro_prizes(block: Tag, record: DrawRecord) -> list[PrizeRecord]:
    prizes: list[PrizeRecord] = []
    for index, row in enumerate(block.select("table.table_max3d tbody tr")):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) != 3:
            continue
        tier = _first_line(cells[0])
        if not tier:
            continue
        amount = _compact_vnd(_text(cells[0].select_one(".gtgt")))
        numbers = [span.get_text(strip=True).zfill(3) for span in cells[1].select("span.kq")]
        note = _text(cells[1].select_one(".kqnote"))
        rule = " ".join(part for part in (note, " ".join(numbers)) if part)
        prizes.append(
            _prize(
                record,
                variant="Max 3D Pro",
                tier=tier,
                rule=rule or None,
                winners=_integer(_text(cells[2])),
                value=amount,
                details={"row_index": index, "raw_row": _text(row)},
            )
        )
    return prizes


def _prize(
    record: DrawRecord,
    *,
    variant: str,
    tier: str,
    rule: str | None,
    winners: int | None,
    value: int | None,
    details: dict[str, Any],
) -> PrizeRecord:
    details["data_source"] = "xosominhngoc_net_vn"
    return PrizeRecord(
        product=record.product,
        draw_id=record.draw_id,
        game_variant=variant,
        prize_tier=tier,
        winning_rule=rule,
        winner_count=winners,
        prize_value_vnd=value,
        details=details,
        source_url=record.source_url,
    )


def _draw_id(spec: ProductSpec, block: Tag) -> str:
    match = re.search(r"#(\d+)", _text(block.select_one(".kyve")))
    if match is None:
        raise RuntimeError("Secondary result block has no draw ID")
    width = 7 if spec.slug in {"keno", "bingo18"} else 5
    return str(int(match.group(1))).zfill(width)


def _draw_date(block: Tag):
    match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", _text(block.select_one(".ngay")))
    if match is None:
        raise RuntimeError("Secondary result block has no draw date")
    return datetime.strptime(match.group(1), "%d/%m/%Y").date()


def _first_line(element: Tag) -> str:
    text = element.get_text("\n", strip=True)
    return text.splitlines()[0].strip() if text else ""


def _text(element: Tag | None) -> str:
    return element.get_text(" ", strip=True) if element is not None else ""


def _integer(value: str) -> int | None:
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def _vnd(value: str) -> int | None:
    return _integer(value)


def _compact_vnd(value: str) -> int | None:
    normalized = value.casefold().replace(" ", "")
    number_match = re.search(r"\d+(?:[.,]\d+)?", normalized)
    if number_match is None:
        return None
    number = float(number_match.group(0).replace(",", "."))
    if "tỷ" in normalized or "ty" in normalized:
        multiplier = 1_000_000_000
    elif "triệu" in normalized or "tr" in normalized:
        multiplier = 1_000_000
    elif "k" in normalized:
        multiplier = 1_000
    else:
        return _vnd(value)
    return int(number * multiplier)
