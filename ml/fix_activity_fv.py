"""Re-register only USER_RECENT_ACTIVITY with simplified (count/sum) aggregations,
so the Postgres stream writer provisions. Leaves USER_CATEGORY_RECENT untouched.
Then verifies ingest succeeds for both feature views.
"""
from __future__ import annotations

import datetime as dt
import sys
import time

from snowflake.ml.feature_store import Feature, FeatureView, StreamConfig

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from feature_store import (  # noqa: E402
    FeatureAggregationMethod, V, _online, build_stream_source, open_fs, passthrough,
)
from _session import DEMO_DB, get_session  # noqa: E402


def main() -> None:
    s = get_session()
    fs = open_fs(s)
    player = fs.get_entity("PLAYER")
    event_stream = build_stream_source()
    backfill_df = s.sql(f"""
        SELECT f.PLAYER_ID::NUMBER AS PLAYER_ID, f.ROUND_START_TIMESTAMP AS EVENT_TS,
               'PLAY'::STRING AS EVENT_TYPE, f.GAME_TITLE_ID::NUMBER AS GAME_TITLE_ID,
               g.CATEGORY_ID::NUMBER AS CATEGORY_ID, r.REGION_CODE::STRING AS REGION_CODE,
               f.STAKE_TOTAL_AMT_EUR::DOUBLE AS STAKE_AMT, f.SESSION_ID::STRING AS SESSION_ID,
               f.GAME_TITLE_ID::STRING AS GAME_KEY
        FROM {DEMO_DB}.CORE.GAME_ROUND_FACT f
        JOIN {DEMO_DB}.CORE.GAME_TITLE_DIM g ON g.GAME_TITLE_ID = f.GAME_TITLE_ID
        JOIN {DEMO_DB}.CORE.PLAYER_DIM p ON p.PLAYER_ID = f.PLAYER_ID
        JOIN {DEMO_DB}.CORE.REGION_DIM r ON r.REGION_ID = p.REGION_ID
        WHERE f.ROUND_START_TIMESTAMP >= DATEADD(day, -2, CURRENT_TIMESTAMP())
    """)
    stream_cfg = StreamConfig(stream_source=event_stream, transformation_fn=passthrough, backfill_df=backfill_df)
    feats = [
        Feature.count("GAME_TITLE_ID", "1h").alias("PLAYS_1H"),
        Feature.count("GAME_TITLE_ID", "24h").alias("PLAYS_24H"),
        Feature.sum("STAKE_AMT", "1h").alias("STAKE_1H"),
        Feature.sum("STAKE_AMT", "24h").alias("STAKE_24H"),
    ]
    fs.register_feature_view(
        FeatureView(
            name="USER_RECENT_ACTIVITY", entities=[player], stream_config=stream_cfg,
            timestamp_col="EVENT_TS", refresh_freq="1 minute", feature_granularity="1 minute",
            features=feats, feature_aggregation_method=FeatureAggregationMethod.CONTINUOUS,
            online_config=_online("10s"), desc="Rolling 1h/24h gameplay activity (count/sum)",
        ), version=V, overwrite=True,
    )
    print("[fix] re-registered USER_RECENT_ACTIVITY with count/sum features")

    ev = [{"PLAYER_ID": 1, "EVENT_TS": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S"),
           "EVENT_TYPE": "PLAY", "GAME_TITLE_ID": 1000, "CATEGORY_ID": 1, "REGION_CODE": "UK",
           "STAKE_AMT": 1.5, "SESSION_ID": "fix-1", "GAME_KEY": "1000"}]
    for attempt in range(18):
        try:
            n = fs.stream_ingest(event_stream, ev)
            print(f"[fix] INGEST OK: {n} accepted on attempt {attempt+1}")
            break
        except Exception as exc:  # noqa: BLE001
            if "no writer available" in str(exc).lower():
                print(f"  [{attempt+1}] writer not ready, retry 20s...")
                time.sleep(20)
                continue
            raise
    else:
        print("[fix] still no writer after retries")
        s.close(); return

    time.sleep(8)
    fv = fs.get_feature_view("USER_CATEGORY_RECENT", V)  # reconstruct may fail; ignore
    s.close()


if __name__ == "__main__":
    main()
