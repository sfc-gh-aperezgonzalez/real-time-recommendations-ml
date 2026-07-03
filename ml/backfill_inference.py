"""Populate ML.RANKER_INFERENCE_LOG (multi-day history) and ML.RANKER_BASELINE
with REAL predictions from the deployed PLAYNOVA_RANKER_SVC.

Everything logged is genuine: feature rows come from the exact same Snowflake
assembly query the live orchestrator uses, and scores come from the deployed
inference service (public ingress + PAT). Only EVENT_TS is backdated so the
daily-aggregated Model Monitor shows a populated multi-day / multi-region time
series for the demo recording. No scores or metrics are fabricated.

Usage:
    python ml/backfill_inference.py --days 7 --players-per-region-per-day 2
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import uuid

import requests

from _session import DEMO_DB, get_session

FEATURE_COLS = ["AFF_FOR_CATEGORY", "GAME_ROUNDS_30D_NORM", "POPULARITY_TREND", "RTP_FRAC", "PLAYER_ROUNDS_30D", "RECENT_CAT_ACTIVITY_NORM"]
RANKER_SERVICE = "PLAYNOVA_RECS_DEMO.ML.PLAYNOVA_RANKER_SVC"


def read_pat() -> str:
    # Prefer the SNOWFLAKE_PAT env var; otherwise read the gitignored token file
    # at the repo root (written by ml/create_pat_secret.py).
    env = os.environ.get("SNOWFLAKE_PAT")
    if env:
        return env.strip()
    token_file = os.path.join(os.path.dirname(__file__), "..", ".pat_token")
    with open(token_file) as f:
        return f.read().strip()


def ingress_url(session) -> str:
    rows = session.sql(f"SHOW ENDPOINTS IN SERVICE {RANKER_SERVICE}").collect()
    for r in rows:
        d = r.as_dict()
        if str(d.get("name", "")).lower() == "inference":
            return f"https://{d['ingress_url']}"
    raise RuntimeError("no inference endpoint ingress_url found")


# Identical feature computation to services/orchestrator/app.py:assemble_candidates
# (eligible, non-excluded candidates only -- the rows the model actually scores).
# ref_ts backdates the point-in-time recency feature to the row's EVENT_TS so the
# logged features are internally consistent with the backdated prediction time.
def assemble(session, pid: int, region: str, ref_ts: str) -> list[dict]:
    rows = session.sql(f"""
        WITH aff AS (SELECT * FROM {DEMO_DB}.FEATURES.PLAYER_AFFINITY_PROFILE WHERE PLAYER_ID = {pid}),
             beh AS (SELECT ROUNDS_30D FROM {DEMO_DB}.FEATURES.PLAYER_BEHAVIOR_PROFILE WHERE PLAYER_ID = {pid}),
             catrec AS (  -- recent 24h play count per category as-of ref_ts (same 24h-count the online FV serves)
                 SELECT rg.CATEGORY_ID AS CATEGORY_ID, COUNT(*) AS CNT
                 FROM {DEMO_DB}.CORE.GAME_ROUND_FACT rf
                 JOIN {DEMO_DB}.CORE.GAME_TITLE_DIM rg ON rg.GAME_TITLE_ID = rf.GAME_TITLE_ID
                 WHERE rf.PLAYER_ID = {pid}
                   AND rf.ROUND_START_TIMESTAMP >= DATEADD('hour', -24, TO_TIMESTAMP_NTZ('{ref_ts}'))
                   AND rf.ROUND_START_TIMESTAMP <  TO_TIMESTAMP_NTZ('{ref_ts}')
                 GROUP BY rg.CATEGORY_ID
             )
        SELECT
            COALESCE(CASE g.CATEGORY_ID
                WHEN 1 THEN aff.AFF_SLOTS WHEN 2 THEN aff.AFF_JACKPOT WHEN 3 THEN aff.AFF_CLASSIC
                WHEN 4 THEN aff.AFF_TABLE WHEN 5 THEN aff.AFF_SCRATCH WHEN 6 THEN aff.AFF_LIVE_ROULETTE
                WHEN 7 THEN aff.AFF_LIVE_BLACKJACK WHEN 8 THEN aff.AFF_LIVE_BACCARAT WHEN 9 THEN aff.AFF_GAME_SHOW
                WHEN 10 THEN aff.AFF_SPORTSBOOK WHEN 11 THEN aff.AFF_ESPORTS WHEN 12 THEN aff.AFF_MEGAWAYS
                ELSE aff.AFF_SLOTS END, 0)                                     AS AFF_FOR_CATEGORY,
            ZEROIFNULL(p.ROUNDS_30D) / NULLIF(MAX(ZEROIFNULL(p.ROUNDS_30D)) OVER (), 0) AS GAME_ROUNDS_30D_NORM,
            ZEROIFNULL(p.POPULARITY_TREND)                                     AS POPULARITY_TREND,
            ZEROIFNULL(g.RETURN_TO_PLAYER_PCT) / 100.0                         AS RTP_FRAC,
            COALESCE((SELECT ROUNDS_30D FROM beh), 0)                          AS PLAYER_ROUNDS_30D,
            LEAST(ZEROIFNULL(cr.CNT), 10) / 10.0                               AS RECENT_CAT_ACTIVITY_NORM
        FROM {DEMO_DB}.CORE.GAME_TITLE_DIM g
        JOIN {DEMO_DB}.CORE.GAME_CATEGORY_DIM c ON c.CATEGORY_ID = g.CATEGORY_ID
        LEFT JOIN {DEMO_DB}.FEATURES.GAME_CATALOG_PROFILE p ON p.GAME_TITLE_ID = g.GAME_TITLE_ID
        LEFT JOIN aff ON TRUE
        LEFT JOIN catrec cr ON cr.CATEGORY_ID = g.CATEGORY_ID
        LEFT JOIN {DEMO_DB}.APP.MARKET_GAME_BLOCK blk ON blk.REGION_CODE='{region}' AND blk.GAME_TITLE_ID=g.GAME_TITLE_ID
        LEFT JOIN {DEMO_DB}.APP.MARKET_CATEGORY_EXCLUSION mce ON mce.REGION_CODE='{region}' AND mce.CATEGORY_ID=g.CATEGORY_ID
        LEFT JOIN {DEMO_DB}.APP.PLAYER_CATEGORY_EXCLUSION pce ON pce.PLAYER_ID={pid} AND pce.CATEGORY_ID=g.CATEGORY_ID
        LEFT JOIN {DEMO_DB}.APP.PLAYER_SUBVERTICAL_EXCLUSION pse ON pse.PLAYER_ID={pid} AND pse.SUBVERTICAL=c.SUBVERTICAL
        WHERE g.AVAILABLE_FOR_PLAY_YN = TRUE
          AND (g.IS_GLOBAL_YN = TRUE OR g.HOME_REGION_CODE = '{region}')
          AND blk.GAME_TITLE_ID IS NULL AND mce.CATEGORY_ID IS NULL
          AND pce.CATEGORY_ID IS NULL AND pse.SUBVERTICAL IS NULL
    """).collect()
    return [r.as_dict() for r in rows]


def score(url: str, pat: str, rows: list[dict]) -> list[float]:
    payload = {"dataframe_split": {
        "index": list(range(len(rows))),
        "columns": FEATURE_COLS,
        "data": [[float(r[k] or 0.0) for k in FEATURE_COLS] for r in rows],
    }}
    resp = requests.post(f"{url}/predict-proba",
                         headers={"Authorization": f'Snowflake Token="{pat}"', "Content-Type": "application/json"},
                         json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()["data"]
    return [float(row[-1]["output_feature_1"]) for row in data]


def sample_players(session, per_region: int) -> list[tuple[int, str]]:
    rows = session.sql(f"""
        SELECT PLAYER_ID, REGION_CODE FROM (
            SELECT p.PLAYER_ID, r.REGION_CODE,
                   ROW_NUMBER() OVER (PARTITION BY r.REGION_CODE ORDER BY p.PLAYER_ID) AS rn
            FROM {DEMO_DB}.CORE.PLAYER_DIM p
            JOIN {DEMO_DB}.CORE.REGION_DIM r ON r.REGION_ID = p.REGION_ID
            WHERE p.IS_ACTIVE
        ) WHERE rn <= {per_region} ORDER BY REGION_CODE, PLAYER_ID
    """).collect()
    return [(int(r["PLAYER_ID"]), r["REGION_CODE"]) for r in rows]


def insert_log(session, batch: list[tuple]) -> None:
    if not batch:
        return
    session.sql(
        f"INSERT INTO {DEMO_DB}.ML.RANKER_INFERENCE_LOG "
        "(ROW_ID,EVENT_TS,AFF_FOR_CATEGORY,GAME_ROUNDS_30D_NORM,POPULARITY_TREND,RTP_FRAC,"
        "PLAYER_ROUNDS_30D,RECENT_CAT_ACTIVITY_NORM,SCORE,REGION_CODE) VALUES "
        + ",".join(["(?,?,?,?,?,?,?,?,?,?)"] * len(batch)),
        params=[v for row in batch for v in row],
    ).collect()


def insert_baseline(session, batch: list[tuple]) -> None:
    if not batch:
        return
    session.sql(
        f"INSERT INTO {DEMO_DB}.ML.RANKER_BASELINE "
        "(AFF_FOR_CATEGORY,GAME_ROUNDS_30D_NORM,POPULARITY_TREND,RTP_FRAC,PLAYER_ROUNDS_30D,RECENT_CAT_ACTIVITY_NORM,SCORE,REGION_CODE) VALUES "
        + ",".join(["(?,?,?,?,?,?,?,?)"] * len(batch)),
        params=[v for row in batch for v in row],
    ).collect()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--players-per-region-per-day", type=int, default=2)
    ap.add_argument("--fresh", action="store_true", help="truncate log + baseline first")
    ap.add_argument("--create-monitor", action="store_true",
                    help="create the model monitor after the baseline is populated")
    args = ap.parse_args()
    rng = random.Random(42)

    session = get_session()
    try:
        if args.fresh:
            session.sql(f"TRUNCATE TABLE IF EXISTS {DEMO_DB}.ML.RANKER_INFERENCE_LOG").collect()
            session.sql(f"TRUNCATE TABLE IF EXISTS {DEMO_DB}.ML.RANKER_BASELINE").collect()
        url = ingress_url(session)
        pat = read_pat()
        print(f"[backfill] inference endpoint: {url}")
        # A pool of players per region; each day scores a rotating slice of them.
        pool = sample_players(session, args.days * args.players_per_region_per_day)
        by_region: dict[str, list[int]] = {}
        for pid, reg in pool:
            by_region.setdefault(reg, []).append(pid)

        total = 0
        baseline_rows: list[tuple] = []
        today = dt.datetime.utcnow().replace(microsecond=0)
        for d in range(args.days, 0, -1):
            day_players: list[tuple[int, str]] = []
            for reg, pids in by_region.items():
                start = (args.days - d) * args.players_per_region_per_day
                day_players += [(pid, reg) for pid in pids[start:start + args.players_per_region_per_day]]
            for pid, reg in day_players:
                ts = today - dt.timedelta(days=d, hours=rng.randint(0, 20), minutes=rng.randint(0, 59))
                rows = assemble(session, pid, reg, ts.strftime("%Y-%m-%d %H:%M:%S"))
                if not rows:
                    continue
                scores = score(url, pat, rows)
                batch = [(str(uuid.uuid4()), ts,
                          float(r["AFF_FOR_CATEGORY"] or 0), float(r["GAME_ROUNDS_30D_NORM"] or 0),
                          float(r["POPULARITY_TREND"] or 0), float(r["RTP_FRAC"] or 0),
                          float(r["PLAYER_ROUNDS_30D"] or 0), float(r["RECENT_CAT_ACTIVITY_NORM"] or 0),
                          float(s), reg)
                         for r, s in zip(rows, scores)]
                insert_log(session, batch)
                total += len(batch)
                # Reference/baseline snapshot from the earliest day only.
                if d == args.days:
                    baseline_rows += [(b[2], b[3], b[4], b[5], b[6], b[7], b[8], b[9]) for b in batch]
            print(f"[backfill] day -{d}: cumulative rows={total}")

        # Baseline: cap to a representative sample for drift comparison.
        rng.shuffle(baseline_rows)
        insert_baseline(session, baseline_rows[:3000])
        print(f"[backfill] DONE: log rows={total}, baseline rows={min(len(baseline_rows),3000)}")

        if args.create_monitor:
            session.sql(f"""
                CREATE OR REPLACE MODEL MONITOR {DEMO_DB}.ML.PLAYNOVA_RANKER_MONITOR WITH
                    MODEL = {DEMO_DB}.ML.PLAYNOVA_RANKER
                    VERSION = 'V2'
                    FUNCTION = 'predict'
                    SOURCE = {DEMO_DB}.ML.RANKER_INFERENCE_LOG
                    BASELINE = {DEMO_DB}.ML.RANKER_BASELINE
                    WAREHOUSE = COMPUTE_WH
                    REFRESH_INTERVAL = '1 hour'
                    AGGREGATION_WINDOW = '1 day'
                    TIMESTAMP_COLUMN = EVENT_TS
                    ID_COLUMNS = ( 'ROW_ID' )
                    PREDICTION_SCORE_COLUMNS = ( 'SCORE' )
                    SEGMENT_COLUMNS = ( 'REGION_CODE' )
            """).collect()
            print("[backfill] model monitor PLAYNOVA_RANKER_MONITOR created")
    finally:
        session.close()


if __name__ == "__main__":
    main()
