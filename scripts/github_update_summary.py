from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def render_summary(
    update_report: dict[str, Any] | None,
    validation_report: dict[str, Any] | None,
) -> str:
    lines = ["## Kết quả cập nhật Vietlott", ""]
    if update_report is None:
        lines.extend(
            [
                "Không tìm thấy báo cáo cập nhật. Hãy xem log của bước thu thập.",
                "",
            ]
        )
    else:
        products = ", ".join(update_report.get("products", [])) or "không xác định"
        fallbacks = sorted(update_report.get("secondary_fallback", {}))
        lines.extend(
            [
                "| Chỉ số | Giá trị |",
                "| --- | ---: |",
                f"| Sản phẩm | {products} |",
                f"| Kỳ mới | {int(update_report.get('new_draw_rows', 0)):,} |",
                f"| Dòng giải thưởng mới | {int(update_report.get('new_prize_rows', 0)):,} |",
                f"| Tổng kỳ sau cập nhật | {int(update_report.get('draw_rows_after', 0)):,} |",
                f"| Lỗi nguồn chưa xử lý | {int(update_report.get('source_errors', 0)):,} |",
                (
                    "| Lỗi truy cập nguồn chính thức | "
                    f"{int(update_report.get('official_source_errors', 0)):,} |"
                ),
                "",
                "Nguồn dự phòng đã dùng "
                + (", ".join(fallbacks) if fallbacks else "không có"),
                "",
            ]
        )

    if validation_report is None:
        lines.extend(
            [
                "Không tìm thấy báo cáo kiểm tra dataset.",
                "",
            ]
        )
    else:
        valid = bool(validation_report.get("valid"))
        errors = validation_report.get("errors", [])
        lines.extend(
            [
                f"Kiểm tra dataset {'đạt' if valid else 'không đạt'}.",
                "",
                f"- Số kỳ {int(validation_report.get('draw_rows', 0)):,}",
                f"- Số dòng giải thưởng {int(validation_report.get('prize_rows', 0)):,}",
                f"- Số lỗi {len(errors)}",
                "",
            ]
        )
        if errors:
            lines.extend(f"- {error}" for error in errors[:20])
            lines.append("")
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-report", type=Path, required=True)
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    summary = render_summary(
        _read_json(args.update_report),
        _read_json(args.validation_report),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(summary)
        handle.write("\n")


if __name__ == "__main__":
    main()
