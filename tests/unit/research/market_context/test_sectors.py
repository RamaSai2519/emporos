"""EM-239: the industry -> sector index map; missing stays missing."""

from __future__ import annotations

from pathlib import Path

from emporos.research.market_context.sectors import SectorMapLoader


def test_maps_known_industries_and_leaves_the_rest_missing(tmp_path: Path) -> None:
    table = tmp_path / "t.csv"
    table.write_text("industry,index_id,index_name\nInformation Technology,NSE:8,NIFTY IT\n")
    files = tmp_path / "c.csv"
    files.write_text(
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "A,Information Technology,AAA,EQ,I1\nB,Capital Goods,BBB,EQ,I2\n"
    )

    sectors = SectorMapLoader(table).load([files])

    assert sectors.of("AAA") == ("NSE:8", "NIFTY IT")
    assert sectors.of("BBB") is None and sectors.of("ZZZ") is None


def test_the_committed_table_covers_the_d1_files() -> None:
    sectors = SectorMapLoader().load(
        [Path("config/universe/d1/nifty100.csv"), Path("config/universe/d1/niftymidcap150.csv")]
    )
    assert sectors.of("TCS") == ("NSE:99926008", "NIFTY IT")
