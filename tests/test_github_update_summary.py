from scripts.github_update_summary import render_summary


def test_render_summary_reports_fallback_and_validation() -> None:
    summary = render_summary(
        {
            "products": ["keno", "bingo18"],
            "new_draw_rows": 2,
            "new_prize_rows": 0,
            "draw_rows_after": 379_315,
            "source_errors": 0,
            "official_source_errors": 2,
            "secondary_fallback": {"keno": {}, "bingo18": {}},
        },
        {
            "valid": True,
            "draw_rows": 379_315,
            "prize_rows": 45_320,
            "errors": [],
        },
    )

    assert "Kỳ mới | 2" in summary
    assert "bingo18, keno" in summary
    assert "Kiểm tra dataset đạt" in summary
