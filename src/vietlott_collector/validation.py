from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .config import ParserKind, ProductSpec
from .models import DrawRecord


def _integers(value: Any) -> list[int]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, int)]
    return []


def validate_draw(record: DrawRecord, spec: ProductSpec) -> list[str]:
    warnings: list[str] = []
    if not record.draw_id.isdigit():
        warnings.append("draw_id is not numeric")
    else:
        expected_width = 7 if spec.slug in {"keno", "bingo18"} else 5
        if len(record.draw_id) != expected_width:
            warnings.append(
                f"draw_id expected {expected_width} digits, got {len(record.draw_id)}"
            )

    if spec.parser_kind in {ParserKind.MATRIX, ParserKind.KENO}:
        numbers = _integers(record.result.get("numbers"))
        _validate_number_set(
            warnings,
            numbers,
            expected_count=spec.main_count,
            minimum=spec.main_min,
            maximum=spec.main_max,
            require_unique=True,
            label="numbers",
        )
        special_numbers = _integers(record.result.get("special_numbers"))
        if spec.special_count:
            _validate_number_set(
                warnings,
                special_numbers,
                expected_count=spec.special_count,
                minimum=spec.special_min,
                maximum=spec.special_max,
                require_unique=False,
                label="special_numbers",
            )
        if spec.slug == "power655" and set(numbers) & set(special_numbers):
            warnings.append("Power 6/55 special number duplicates a main number")
    elif spec.parser_kind is ParserKind.BINGO:
        digits = _integers(record.result.get("digits"))
        _validate_number_set(
            warnings,
            digits,
            expected_count=3,
            minimum=0,
            maximum=9,
            require_unique=False,
            label="digits",
        )
        expected_total = sum(digits)
        actual_total = record.attributes.get("total")
        if isinstance(actual_total, int) and actual_total != expected_total:
            warnings.append(f"total={actual_total} does not match digit sum={expected_total}")
    elif spec.parser_kind is ParserKind.THREE_DIGIT:
        tiers = record.result.get("tiers")
        expected_sizes = {
            "special": 2,
            "first": 4,
            "second": 6,
            "third": 8,
        }
        if not isinstance(tiers, dict):
            warnings.append("tiers is missing")
        else:
            for tier, expected_size in expected_sizes.items():
                values = tiers.get(tier)
                if not isinstance(values, list) or len(values) != expected_size:
                    warnings.append(f"tiers.{tier} expected {expected_size} values")
                    continue
                invalid = [value for value in values if not _valid_three_digit(value)]
                if invalid:
                    warnings.append(f"tiers.{tier} has invalid values: {invalid}")
    elif spec.parser_kind is ParserKind.FOUR_DIGIT:
        tiers = record.result.get("tiers")
        expected = {
            "first": (1, r"\d{4}"),
            "second": (2, r"\d{4}"),
            "third": (3, r"\d{4}"),
            "consolation_1": (1, r"X\d{3}"),
            "consolation_2": (1, r"XX\d{2}"),
        }
        if not isinstance(tiers, dict):
            warnings.append("tiers is missing")
        else:
            for tier, (expected_size, pattern) in expected.items():
                values = tiers.get(tier)
                if not isinstance(values, list) or len(values) != expected_size:
                    warnings.append(f"tiers.{tier} expected {expected_size} values")
                    continue
                invalid = [
                    value
                    for value in values
                    if not isinstance(value, str) or re.fullmatch(pattern, value) is None
                ]
                if invalid:
                    warnings.append(f"tiers.{tier} has invalid values: {invalid}")

    record.validation_warnings = warnings
    record.validation_status = "valid" if not warnings else "warning"
    return warnings


def _validate_number_set(
    warnings: list[str],
    values: Iterable[int],
    *,
    expected_count: int | None,
    minimum: int | None,
    maximum: int | None,
    require_unique: bool,
    label: str,
) -> None:
    numbers = list(values)
    if expected_count is not None and len(numbers) != expected_count:
        warnings.append(f"{label} expected {expected_count} values, got {len(numbers)}")
    if minimum is not None and maximum is not None:
        outside = [value for value in numbers if not minimum <= value <= maximum]
        if outside:
            warnings.append(f"{label} outside range {minimum}..{maximum}: {outside}")
    if require_unique and len(set(numbers)) != len(numbers):
        warnings.append(f"{label} contains duplicate values")


def _valid_three_digit(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 3 and value.isdigit()
