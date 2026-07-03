"""End-to-end smoke suite for the PlayNova orchestrator (spec section 10).

Runs the orchestrator in-process via FastAPI TestClient (hitting the live
Snowflake account + Online Feature Store), so it validates the real request
path without needing the authenticated SPCS endpoint.

    SNOWFLAKE_PAT="$(cat .pat_token)" .venv/bin/python -m pytest tests/test_smoke.py -v
"""
from __future__ import annotations

import os
import sys
import time
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ml"))
sys.path.insert(0, os.path.join(ROOT, "services", "orchestrator"))

os.environ.setdefault("PLAYNOVA_DB", "PLAYNOVA_RECS_DEMO")
os.environ.setdefault("PLAYNOVA_CONNECTION", "default")

from fastapi.testclient import TestClient  # noqa: E402
import app as orch  # noqa: E402
from _session import DEMO_DB, get_session  # noqa: E402

client = TestClient(orch.app)


@pytest.fixture(scope="module")
def player():
    email = f"smoke_{uuid.uuid4().hex[:8]}@playnova.demo"
    r = client.post("/register", json={"email": email, "password": "pw", "region_code": "UK"})
    assert r.status_code == 200, r.text
    pid = r.json()["player_id"]
    yield {"player_id": pid, "email": email, "region_code": "UK"}
    # cleanup
    s = get_session()
    s.sql(f"DELETE FROM {DEMO_DB}.APP.APP_CREDENTIAL WHERE PLAYER_ID={pid}").collect()
    s.sql(f"DELETE FROM {DEMO_DB}.CORE.PLAYER_DIM WHERE PLAYER_ID={pid}").collect()
    s.close()


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_register_and_login(player):
    r = client.post("/login", json={"email": player["email"], "password": "pw"})
    assert r.json()["player_id"] == player["player_id"]


def test_baseline_recommendations(player):
    r = client.post("/recommendations", json={"player_id": player["player_id"], "region_code": "UK", "top_n": 8})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["candidate_set_size"] > 0
    assert len(body["rails"]["recommended_for_you"]) > 0
    assert body["trace_id"]


def test_fake_play_writes_raw(player):
    r = client.post("/events", json={"player_id": player["player_id"], "event_type": "PLAY",
                                      "game_title_id": 1011, "category_id": 6, "region_code": "UK", "stake_amt": 5.0})
    assert r.json()["raw_persisted"] is True
    s = get_session()
    cnt = s.sql(f"""SELECT COUNT(*) FROM {DEMO_DB}.RAW.GAMEPLAY_EVENTS
                    WHERE PLAYER_ID={player['player_id']} AND EVENT_TYPE='PLAY'""").collect()[0][0]
    s.close()
    assert cnt >= 1


def test_real_time_signal_changes_recs(player):
    """After playing a category, the because_you_played rail surfaces that category."""
    pid = player["player_id"]
    # play live_roulette (cat 6) a few times
    for _ in range(3):
        client.post("/events", json={"player_id": pid, "event_type": "PLAY", "game_title_id": 1011,
                                     "category_id": 6, "region_code": "UK", "stake_amt": 5.0})
    time.sleep(3)
    r = client.post("/recommendations", json={"player_id": pid, "region_code": "UK", "top_n": 8,
                                              "because_you_played": 1011})
    rails = r.json()["rails"]
    assert "because_you_played" in rails and len(rails["because_you_played"]) > 0
    assert all(c["category"] == "Live Roulette" for c in rails["because_you_played"])


def test_market_block_suppresses_game(player):
    """A Streamlit-style market block must remove a game from the candidate set."""
    pid, s = player["player_id"], get_session()
    game = s.sql(f"""SELECT e.GAME_TITLE_ID FROM {DEMO_DB}.FEATURES.MARKET_ELIGIBLE_GAMES e
                     WHERE e.REGION_CODE='UK' AND e.IS_ELIGIBLE=TRUE LIMIT 1""").collect()[0][0]
    before = client.post("/recommendations", json={"player_id": pid, "region_code": "UK", "top_n": 200}).json()
    s.sql(f"""INSERT INTO {DEMO_DB}.APP.MARKET_GAME_BLOCK (REGION_CODE,GAME_TITLE_ID,REASON,UPDATED_BY)
              VALUES ('UK',{game},'smoke test','pytest')""").collect()
    try:
        after = client.post("/recommendations", json={"player_id": pid, "region_code": "UK", "top_n": 200}).json()
        all_after = {c["game_title_id"] for rail in after["rails"].values() for c in rail}
        assert game not in all_after, "blocked game still recommended"
    finally:
        s.sql(f"DELETE FROM {DEMO_DB}.APP.MARKET_GAME_BLOCK WHERE REGION_CODE='UK' AND GAME_TITLE_ID={game}").collect()
        s.close()


def test_player_exclusion_logged(player):
    """Player 1 is seeded with jackpot + live_baccarat exclusions; trace must record suppressions."""
    r = client.post("/recommendations", json={"player_id": 1, "region_code": "UK", "top_n": 12})
    assert r.json()["excluded_count"] >= 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
