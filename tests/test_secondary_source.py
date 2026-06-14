from datetime import date

import pytest

from vietlott_collector.config import PRODUCT_SPECS
from vietlott_collector.incremental_update import _store_secondary_batch
from vietlott_collector.models import DrawRecord, PrizeRecord
from vietlott_collector.sources.secondary import SecondaryBatch, parse_secondary_page
from vietlott_collector.storage import SqliteDatasetStore


def test_parse_secondary_keno_and_bingo() -> None:
    keno_numbers = "".join(
        f'<span class="kq">{number:02d}</span>' for number in range(1, 21)
    )
    keno = parse_secondary_page(
        PRODUCT_SPECS["keno"],
        f"""
        <article class="xskeno">
          <span class="kyve">#0284690</span>
          <span class="ngay">14/06/2026</span>
          <div class="result">{keno_numbers}</div>
        </article>
        """,
        source_url="https://xosominhngoc.net.vn/kqxs-keno",
    )
    bingo = parse_secondary_page(
        PRODUCT_SPECS["bingo18"],
        """
        <article class="xsbingo18">
          <span class="kyve">#0171757</span>
          <span class="ngay">14/06/2026</span>
          <div class="result">
            <span class="kq">5</span><span class="kq">6</span><span class="kq">4</span>
          </div>
        </article>
        """,
        source_url="https://xosominhngoc.net.vn/kqxs-bingo18",
    )

    assert keno.draws[0].result["numbers"] == list(range(1, 21))
    assert keno.draws[0].attributes["official_verification_status"] == "pending"
    assert bingo.draws[0].result["digits"] == [5, 6, 4]
    assert bingo.draws[0].attributes["total"] == 15


def test_parse_secondary_matrix_result_and_prizes() -> None:
    batch = parse_secondary_page(
        PRODUCT_SPECS["power655"],
        """
        <div class="xspower">
          <span class="kyve">#001358</span>
          <span class="ngay">13/06/2026</span>
          <div class="result">
            <span class="kq">02</span><span class="kq">08</span>
            <span class="kq">19</span><span class="kq">33</span>
            <span class="kq">36</span><span class="kq">47</span>
            <span class="kq">42</span>
          </div>
          <table><tbody>
            <tr><td>Jackpot 1</td><td>6 số</td><td>0</td><td>46.360.660.800</td></tr>
            <tr><td>Giải nhất</td><td>5 số</td><td>19</td><td>40.000.000</td></tr>
          </tbody></table>
        </div>
        """,
        source_url="https://xosominhngoc.net.vn/kqxs-power-655",
    )

    assert batch.draws[0].result["numbers"] == [2, 8, 19, 33, 36, 47]
    assert batch.draws[0].result["special_numbers"] == [42]
    assert batch.draws[0].draw_id == "01358"
    assert batch.draws[0].prize_status == "secondary_complete"
    assert batch.prizes[0].prize_value_vnd == 46_360_660_800
    assert batch.prizes[1].winner_count == 19


def test_parse_secondary_max3d_tiers() -> None:
    batch = parse_secondary_page(
        PRODUCT_SPECS["max3d"],
        """
        <article class="xsmax3d">
          <span class="kyve">#001092</span>
          <span class="ngay">12/06/2026</span>
          <table class="table_max3d"><tbody>
            <tr>
              <td>Giải ĐB <span class="giaiMax3d">1Tr</span>
                  <span class="max3d_sl">(38)</span></td>
              <td class="max3d_number"><span class="kq">350</span><span class="kq">839</span></td>
              <td>Giải ĐB <span class="giaiMax3d">1Tỷ</span>
                  <span class="max3d_sl">(0)</span></td>
            </tr>
            <tr>
              <td>Giải Nhất <span class="giaiMax3d">350K</span>
                  <span class="max3d_sl">(33)</span></td>
              <td class="max3d_number">
                <span class="kq">975</span><span class="kq">955</span>
                <span class="kq">069</span><span class="kq">405</span>
              </td><td>Giải Nhất <span class="giaiMax3d">40Tr</span></td>
            </tr>
            <tr>
              <td>Giải Nhì</td><td class="max3d_number">
                <span class="kq">896</span><span class="kq">451</span>
                <span class="kq">810</span><span class="kq">851</span>
                <span class="kq">841</span><span class="kq">328</span>
              </td><td>Giải Nhì</td>
            </tr>
            <tr>
              <td>Giải Ba</td><td class="max3d_number">
                <span class="kq">397</span><span class="kq">888</span>
                <span class="kq">977</span><span class="kq">204</span>
                <span class="kq">623</span><span class="kq">070</span>
                <span class="kq">140</span><span class="kq">332</span>
              </td><td>Giải Ba</td>
            </tr>
          </tbody></table>
        </article>
        """,
        source_url="https://xosominhngoc.net.vn/kqxs-max3d",
    )

    assert batch.draws[0].result["tiers"]["special"] == ["350", "839"]
    assert batch.draws[0].result["tiers"]["third"][-1] == "332"
    assert batch.prizes[0].prize_value_vnd == 1_000_000


def test_secondary_store_rejects_a_conflicting_existing_draw(tmp_path) -> None:
    source_url = "https://xosominhngoc.net.vn/kqxs-power-655"
    record = DrawRecord(
        product="power655",
        draw_id="01359",
        draw_date=date(2026, 6, 16),
        result={"numbers": [1, 2, 3, 4, 5, 6], "special_numbers": [7]},
        source_url=source_url,
        prize_status="secondary_complete",
        validation_status="valid",
    )
    prize = PrizeRecord(
        product="power655",
        draw_id="01359",
        game_variant="Giá trị Jackpot 2",
        prize_tier="Jackpot 1",
        winning_rule="6 số",
        winner_count=0,
        prize_value_vnd=50_000_000_000,
        details={"data_source": "xosominhngoc_net_vn"},
        source_url=source_url,
    )
    store = SqliteDatasetStore(tmp_path)
    try:
        report = _store_secondary_batch(
            store,
            SecondaryBatch(draws=[record], prizes=[prize], source_url=source_url),
        )
        assert report["draws_inserted"] == 1

        conflicting = DrawRecord(
            product="power655",
            draw_id="01359",
            draw_date=date(2026, 6, 16),
            result={"numbers": [1, 2, 3, 4, 5, 8], "special_numbers": [7]},
            source_url=source_url,
            prize_status="secondary_complete",
            validation_status="valid",
        )
        with pytest.raises(RuntimeError, match="conflicts with stored draw"):
            _store_secondary_batch(
                store,
                SecondaryBatch(
                    draws=[conflicting],
                    prizes=[prize],
                    source_url=source_url,
                ),
            )
    finally:
        store.close()
