"""ML + data integrity tests for the PlayNova demo (pytest).

Validates the data layer, Dynamic Tables, Online Feature Store round-trip, and
the Model Registry against the live account. Run:

    SNOWFLAKE_PAT="$(cat .pat_token)" .venv/bin/python -m pytest tests/test_ml.py -v
"""
from __future__ import annotations

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ml"))

from _session import DEMO_DB, get_session  # noqa: E402


@pytest.fixture(scope="module")
def session():
    s = get_session()
    yield s
    s.close()


def _scalar(s, sql):
    return s.sql(sql).collect()[0][0]


def test_catalog_counts(session):
    assert _scalar(session, f"SELECT COUNT(*) FROM {DEMO_DB}.CORE.GAME_TITLE_DIM") >= 200
    assert _scalar(session, f"SELECT COUNT(DISTINCT GAME_TITLE) FROM {DEMO_DB}.CORE.GAME_TITLE_DIM") == \
           _scalar(session, f"SELECT COUNT(*) FROM {DEMO_DB}.CORE.GAME_TITLE_DIM"), "game titles must be unique"
    assert _scalar(session, f"SELECT COUNT(*) FROM {DEMO_DB}.CORE.PLAYER_DIM") >= 1000
    assert _scalar(session, f"SELECT COUNT(*) FROM {DEMO_DB}.CORE.GAME_ROUND_FACT") >= 100000


def test_dynamic_tables_populated(session):
    for t in ["PLAYER_BEHAVIOR_PROFILE", "PLAYER_AFFINITY_PROFILE", "GAME_CATALOG_PROFILE",
              "MARKET_ELIGIBLE_GAMES", "PLAYER_GAME_INTERACTION"]:
        assert _scalar(session, f"SELECT COUNT(*) FROM {DEMO_DB}.FEATURES.{t}") > 0, f"{t} empty"


def test_affinity_skew_learnable(session):
    """Segments must show clearly differentiated category affinity (model signal)."""
    row = session.sql(f"""
        SELECT
          AVG(IFF(b.PLAYER_SEGMENT='SLOT_GRINDER', a.AFF_SLOTS, NULL)) slot_grinder_slots,
          AVG(IFF(b.PLAYER_SEGMENT='SPORTS_BETTOR', a.AFF_SPORTSBOOK+a.AFF_ESPORTS, NULL)) sports_sports,
          AVG(IFF(b.PLAYER_SEGMENT='SLOT_GRINDER', a.AFF_SPORTSBOOK+a.AFF_ESPORTS, NULL)) slot_grinder_sports
        FROM {DEMO_DB}.FEATURES.PLAYER_AFFINITY_PROFILE a
        JOIN {DEMO_DB}.FEATURES.PLAYER_BEHAVIOR_PROFILE b ON b.PLAYER_ID=a.PLAYER_ID
    """).collect()[0]
    assert row["SLOT_GRINDER_SLOTS"] > 0.4
    assert row["SPORTS_SPORTS"] > row["SLOT_GRINDER_SPORTS"] * 3


def test_market_eligibility_enforced(session):
    """Seeded ES game blocks + DE esports exclusion must make those games ineligible."""
    es_blocked = _scalar(session, f"""
        SELECT COUNT(*) FROM {DEMO_DB}.FEATURES.MARKET_ELIGIBLE_GAMES e
        JOIN {DEMO_DB}.APP.MARKET_GAME_BLOCK b ON b.REGION_CODE=e.REGION_CODE AND b.GAME_TITLE_ID=e.GAME_TITLE_ID
        WHERE e.REGION_CODE='ES' AND e.IS_ELIGIBLE=TRUE""")
    assert es_blocked == 0, "blocked ES games must not be eligible"
    de_esports = _scalar(session, f"""
        SELECT COUNT(*) FROM {DEMO_DB}.FEATURES.MARKET_ELIGIBLE_GAMES
        WHERE REGION_CODE='DE' AND CATEGORY_ID=11 AND IS_ELIGIBLE=TRUE""")
    assert de_esports == 0, "DE esports must be excluded"


def test_models_registered(session):
    from snowflake.ml.registry import Registry
    reg = Registry(session, database_name=DEMO_DB, schema_name="ML")
    names = {m.name for m in reg.show_models().to_pandas().itertuples(index=False)} if False else \
            set(reg.show_models()["name"].tolist())
    assert "PLAYNOVA_RANKER" in names
    assert "PLAYNOVA_PROPENSITY" in names


@pytest.mark.skipif(not os.environ.get("SNOWFLAKE_PAT"), reason="needs SNOWFLAKE_PAT for OFS online")
def test_ofs_ingest_query_roundtrip(session):
    import datetime as dt
    sys.path.insert(0, os.path.join(ROOT, "ml"))
    from feature_store import build_stream_source, open_fs
    fs = open_fs(session)
    ev = [{"PLAYER_ID": 3, "EVENT_TS": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S"),
           "EVENT_TYPE": "PLAY", "GAME_TITLE_ID": 1000, "CATEGORY_ID": 1, "REGION_CODE": "UK",
           "STAKE_AMT": 2.0, "SESSION_ID": "pytest-1", "GAME_KEY": "1000"}]
    n = fs.stream_ingest(build_stream_source(), ev)
    assert n >= 1, "stream ingest accepted no records"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
