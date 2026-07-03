"""PlayNova Online Feature Store setup (centerpiece).

Builds the Snowflake Feature Store + Online Feature Store (Postgres-backed):
  - Entities: PLAYER, GAME
  - Online service (auto-provisions a dedicated managed Postgres) -> net-zero
  - Batch feature views (Postgres online): player behavior, player affinity, game profile
  - Stream feature views (continuous, Postgres online):
        * USER_RECENT_ACTIVITY   - rolling 1h/24h play counts, stake, unique games
        * USER_CATEGORY_RECENT    - per-CATEGORY_ID recent counts (secondary key agg)
  - Real-time feature view: request-context-weighted activity
  - Feature group PLAYER_REC_FG: player-keyed bundle for training-serving parity

CLI:
    python ml/feature_store.py all          # entities -> online -> all FVs -> group
    python ml/feature_store.py online        # create + wait for online service
    python ml/feature_store.py register      # entities + all feature views + group
    python ml/feature_store.py status
    python ml/feature_store.py ingest-test    # push a synthetic event + read back
    python ml/feature_store.py teardown       # drop online service + feature views
"""
from __future__ import annotations

import argparse
import sys
import time

import pandas as pd
from snowflake.ml.feature_store import (
    CreationMode,
    Entity,
    Feature,
    FeatureGroup,
    FeatureStore,
    FeatureView,
    OnlineConfig,
    OnlineStoreType,
    RealtimeConfig,
    RequestSource,
    StreamConfig,
    StreamSource,
    online_service,
)
from snowflake.snowpark.types import (
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampTimeZone,
    TimestampType,
)

from _session import DEMO_DB, DEMO_WH, get_session

FS_NAME = "FEATURES"
PRODUCER_ROLE = "PLAYNOVA_FS_PRODUCER"
CONSUMER_ROLE = "PLAYNOVA_FS_CONSUMER"
STREAM_SOURCE = "PLAYNOVA_GAMEPLAY_EVENTS"
V = "V1"

try:
    from snowflake.ml.feature_store.spec.enums import FeatureAggregationMethod
except Exception:  # pragma: no cover
    from snowflake.ml.feature_store import FeatureAggregationMethod  # type: ignore


# Stream transform must be a named, module-level function (no lambdas).
def passthrough(df: pd.DataFrame) -> pd.DataFrame:
    return df


# Real-time compute must be a named, module-level function.
def context_adjusted_activity(request_df: pd.DataFrame, behavior_df: pd.DataFrame) -> pd.DataFrame:
    weight = request_df["CONTEXT_WEIGHT"].astype(float).reset_index(drop=True)
    rounds = behavior_df["ROUNDS_30D"].fillna(0).astype(float).reset_index(drop=True)
    return pd.DataFrame({"CONTEXT_ADJUSTED_ACTIVITY": rounds * weight})


def open_fs(session) -> FeatureStore:
    return FeatureStore(
        session=session,
        database=DEMO_DB,
        name=FS_NAME,
        default_warehouse=DEMO_WH,
        creation_mode=CreationMode.CREATE_IF_NOT_EXIST,
    )


def register_entities(fs: FeatureStore) -> tuple[Entity, Entity]:
    player = Entity(name="PLAYER", join_keys=["PLAYER_ID"], desc="Registered PlayNova player")
    game = Entity(name="GAME", join_keys=["GAME_TITLE_ID"], desc="Game title in the catalog")
    fs.register_entity(player)
    fs.register_entity(game)
    print("[fs] entities registered: PLAYER, GAME")
    return player, game


def create_online(fs: FeatureStore) -> None:
    print("[fs] creating online service (provisions dedicated managed Postgres)...")
    try:
        res = fs.create_online_service(PRODUCER_ROLE, CONSUMER_ROLE)
        print(f"[fs] create result: {res}")
    except Exception as exc:  # noqa: BLE001
        if "already exists" in str(exc).lower():
            print("[fs] online service already exists - reusing")
        else:
            raise
    for i in range(40):
        status = fs.get_online_service_status()
        eps = [e.name for e in status.endpoints] if status.endpoints else []
        print(f"  [{i}] status={status.status} endpoints={eps}")
        if status.status == "RUNNING" and eps:
            print(f"[fs] ONLINE SERVICE RUNNING. query={online_service.endpoint_url(status,'query')}")
            return
        time.sleep(30)
    raise TimeoutError("online service did not reach RUNNING in time")


def _online(target_lag: str = "30s") -> OnlineConfig:
    return OnlineConfig(enable=True, target_lag=target_lag, store_type=OnlineStoreType.POSTGRES)


def register_batch_fvs(fs: FeatureStore, session, player: Entity, game: Entity) -> None:
    # 1. Player behavior profile
    behavior_df = session.sql(f"""
        SELECT PLAYER_ID, TOTAL_ROUNDS, DISTINCT_GAMES, ROUNDS_7D, ROUNDS_30D, ROUNDS_90D,
               TOTAL_STAKE_EUR, AVG_STAKE_EUR, TOTAL_GGR_EUR, DAYS_SINCE_LAST_PLAY,
               PLAYER_SEGMENT, PREF_VERTICAL, PREF_SUBVERTICAL, REGION_CODE,
               COALESCE(LAST_ACTIVITY_TS, '2020-01-01'::TIMESTAMP_NTZ) AS LAST_ACTIVITY_TS
        FROM {DEMO_DB}.FEATURES.PLAYER_BEHAVIOR_PROFILE
    """)
    fs.register_feature_view(
        FeatureView(
            name="PLAYER_BEHAVIOR_FV", entities=[player], feature_df=behavior_df,
            timestamp_col="LAST_ACTIVITY_TS", refresh_freq="60 minute",
            online_config=_online(), desc="Slower-changing per-player behavior features",
        ), version=V, overwrite=True,
    )
    print("[fs] registered PLAYER_BEHAVIOR_FV")

    # 2. Player affinity profile (no natural timestamp -> latest processed wins)
    affinity_df = session.sql(f"""
        SELECT PLAYER_ID, AFFINITY_WEIGHT_TOTAL,
               AFF_SLOTS, AFF_JACKPOT, AFF_CLASSIC, AFF_TABLE, AFF_SCRATCH,
               AFF_LIVE_ROULETTE, AFF_LIVE_BLACKJACK, AFF_LIVE_BACCARAT, AFF_GAME_SHOW,
               AFF_SPORTSBOOK, AFF_ESPORTS, AFF_MEGAWAYS
        FROM {DEMO_DB}.FEATURES.PLAYER_AFFINITY_PROFILE
    """)
    fs.register_feature_view(
        FeatureView(
            name="PLAYER_AFFINITY_FV", entities=[player], feature_df=affinity_df,
            refresh_freq="60 minute", online_config=_online(),
            desc="Recency-weighted long-term category affinity shares",
        ), version=V, overwrite=True,
    )
    print("[fs] registered PLAYER_AFFINITY_FV")

    # 3. Game catalog profile (game-keyed)
    game_df = session.sql(f"""
        SELECT GAME_TITLE_ID, CATEGORY_ID, VERTICAL, SUBVERTICAL, RETURN_TO_PLAYER_PCT,
               IFF(HAS_JACKPOT_YN, 1, 0) AS HAS_JACKPOT,
               ROUNDS_30D, PLAYERS_30D, GGR_30D_EUR, POPULARITY_TREND,
               COALESCE(LAST_PLAYED_TS, '2020-01-01'::TIMESTAMP_NTZ) AS LAST_PLAYED_TS
        FROM {DEMO_DB}.FEATURES.GAME_CATALOG_PROFILE
    """)
    fs.register_feature_view(
        FeatureView(
            name="GAME_PROFILE_FV", entities=[game], feature_df=game_df,
            timestamp_col="LAST_PLAYED_TS", refresh_freq="60 minute",
            online_config=_online(), desc="Game popularity / trend / GGR features",
        ), version=V, overwrite=True,
    )
    print("[fs] registered GAME_PROFILE_FV")


def build_stream_source() -> StreamSource:
    """The canonical PLAYNOVA_GAMEPLAY_EVENTS stream-source definition.

    Passing this object (not the name) to stream_ingest avoids a cross-session
    get_stream_source metadata lookup quirk in the SDK.
    """
    return StreamSource(
        name=STREAM_SOURCE,
        schema=StructType([
            StructField("PLAYER_ID", LongType()),
            StructField("EVENT_TS", TimestampType(TimestampTimeZone.NTZ)),
            StructField("EVENT_TYPE", StringType()),
            StructField("GAME_TITLE_ID", LongType()),
            StructField("CATEGORY_ID", LongType()),
            StructField("REGION_CODE", StringType()),
            StructField("STAKE_AMT", DoubleType()),
            StructField("SESSION_ID", StringType()),
            StructField("GAME_KEY", StringType()),  # string copy of game id for approx_count_distinct
        ]),
        desc="Real-time PlayNova gameplay events (mirrors RAW.GAMEPLAY_EVENTS)",
    )


def register_stream_fvs(fs: FeatureStore, session, player: Entity) -> None:
    event_stream = build_stream_source()
    # Register the stream source fresh in THIS session. register_feature_view for a
    # stream FV validates the source via get_stream_source, which only resolves in
    # the same session the source was registered (SDK metadata quirk) - so always
    # run this on a clean schema (see ml/reset_hard.py) as one pass.
    fs.register_stream_source(event_stream)
    print(f"[fs] stream source registered: {STREAM_SOURCE}")

    # Backfill last 2 days of PLAY events from history so the windows are warm.
    backfill_df = session.sql(f"""
        SELECT f.PLAYER_ID::NUMBER AS PLAYER_ID,
               f.ROUND_START_TIMESTAMP AS EVENT_TS,
               'PLAY'::STRING AS EVENT_TYPE,
               f.GAME_TITLE_ID::NUMBER AS GAME_TITLE_ID,
               g.CATEGORY_ID::NUMBER AS CATEGORY_ID,
               r.REGION_CODE::STRING AS REGION_CODE,
               f.STAKE_TOTAL_AMT_EUR::DOUBLE AS STAKE_AMT,
               f.SESSION_ID::STRING AS SESSION_ID,
               f.GAME_TITLE_ID::STRING AS GAME_KEY
        FROM {DEMO_DB}.CORE.GAME_ROUND_FACT f
        JOIN {DEMO_DB}.CORE.GAME_TITLE_DIM g ON g.GAME_TITLE_ID = f.GAME_TITLE_ID
        JOIN {DEMO_DB}.CORE.PLAYER_DIM p ON p.PLAYER_ID = f.PLAYER_ID
        JOIN {DEMO_DB}.CORE.REGION_DIM r ON r.REGION_ID = p.REGION_ID
        WHERE f.ROUND_START_TIMESTAMP >= DATEADD(day, -2, CURRENT_TIMESTAMP())
    """)

    stream_cfg = StreamConfig(
        stream_source=event_stream, transformation_fn=passthrough, backfill_df=backfill_df,
    )

    # A. Overall recent activity (no secondary key). Use only count/sum aggregations,
    # which the Postgres online writer supports reliably (max(timestamp) and
    # approx_count_distinct can leave the stream writer unprovisioned in preview).
    activity_feats = [
        Feature.count("GAME_TITLE_ID", "1h").alias("PLAYS_1H"),
        Feature.count("GAME_TITLE_ID", "24h").alias("PLAYS_24H"),
        Feature.sum("STAKE_AMT", "1h").alias("STAKE_1H"),
        Feature.sum("STAKE_AMT", "24h").alias("STAKE_24H"),
    ]
    fs.register_feature_view(
        FeatureView(
            name="USER_RECENT_ACTIVITY", entities=[player], stream_config=stream_cfg,
            timestamp_col="EVENT_TS", refresh_freq="1 minute", feature_granularity="1 minute",
            features=activity_feats, feature_aggregation_method=FeatureAggregationMethod.CONTINUOUS,
            online_config=_online("10s"), desc="Rolling 1h/24h gameplay activity",
        ), version=V, overwrite=True,
    )
    print("[fs] registered USER_RECENT_ACTIVITY (stream)")

    # B. Per-category recent counts via secondary key aggregation
    cat_feats = [
        Feature.count("GAME_TITLE_ID", "24h").alias("CAT_PLAYS_24H"),
        Feature.count("GAME_TITLE_ID", "1h").alias("CAT_PLAYS_1H"),
    ]
    fs.register_feature_view(
        FeatureView(
            name="USER_CATEGORY_RECENT", entities=[player], stream_config=stream_cfg,
            timestamp_col="EVENT_TS", refresh_freq="1 minute", feature_granularity="1 minute",
            features=cat_feats, feature_aggregation_method=FeatureAggregationMethod.CONTINUOUS,
            aggregation_secondary_keys=["CATEGORY_ID"], online_config=_online("10s"),
            desc="Per-category recent play counts (queryable by player)",
        ), version=V, overwrite=True,
    )
    print("[fs] registered USER_CATEGORY_RECENT (stream, secondary key CATEGORY_ID)")


def register_realtime_fv(fs: FeatureStore, player: Entity) -> None:
    behavior = fs.get_feature_view("PLAYER_BEHAVIOR_FV", V)
    request_source = RequestSource(schema=StructType([StructField("CONTEXT_WEIGHT", DoubleType())]))
    rtfv = FeatureView(
        name="USER_CONTEXT_ACTIVITY", entities=[player],
        realtime_config=RealtimeConfig(
            compute_fn=context_adjusted_activity,
            sources=[request_source, behavior.slice(["ROUNDS_30D"])],
            output_schema=StructType([StructField("CONTEXT_ADJUSTED_ACTIVITY", DoubleType())]),
        ),
        desc="Request-context-weighted recent activity",
    )
    fs.register_feature_view(rtfv, version=V, overwrite=True)
    print("[fs] registered USER_CONTEXT_ACTIVITY (realtime)")


def register_feature_group(fs: FeatureStore) -> None:
    behavior = fs.get_feature_view("PLAYER_BEHAVIOR_FV", V)
    affinity = fs.get_feature_view("PLAYER_AFFINITY_FV", V)
    activity = fs.get_feature_view("USER_RECENT_ACTIVITY", V)
    fg = FeatureGroup(
        name="PLAYER_REC_FG",
        features=[behavior, affinity, activity],
        auto_prefix=False,
        desc="Player-keyed bundle for ranker training-serving parity",
    )
    try:
        fs.register_feature_group(fg, "V1")
    except Exception as exc:  # noqa: BLE001
        if "already exists" in str(exc).lower():
            fs.delete_feature_group("PLAYER_REC_FG", "V1")
            fs.register_feature_group(fg, "V1")
        else:
            raise
    print("[fs] registered feature group PLAYER_REC_FG")


def cmd_status(fs: FeatureStore) -> None:
    print("=== Entities ==="); fs.list_entities().show()
    print("=== Feature Views ==="); fs.list_feature_views().select("NAME", "VERSION").show(50)
    try:
        st = fs.get_online_service_status()
        print(f"=== Online Service: {st.status} | {[e.name for e in (st.endpoints or [])]}")
    except Exception as exc:  # noqa: BLE001
        print(f"online service status: {exc}")


def cmd_ingest_test(fs: FeatureStore) -> None:
    import datetime as dt
    ev = [{
        "PLAYER_ID": 1, "EVENT_TS": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "EVENT_TYPE": "PLAY", "GAME_TITLE_ID": 1000, "CATEGORY_ID": 1,
        "REGION_CODE": "UK", "STAKE_AMT": 1.5, "SESSION_ID": "test-1", "GAME_KEY": "1000",
    }]
    src = build_stream_source()
    # Writers provision asynchronously after FV registration; retry until ready.
    n = 0
    for attempt in range(18):
        try:
            n = fs.stream_ingest(src, ev)
            print(f"[fs] ingested {n} event(s) on attempt {attempt+1}")
            break
        except Exception as exc:  # noqa: BLE001
            if "no writer available" in str(exc).lower():
                print(f"  [{attempt+1}] writer not ready yet, retrying in 20s...")
                time.sleep(20)
                continue
            raise
    else:
        raise RuntimeError("stream writer never became available; recreate the online service")
    time.sleep(8)
    fv = fs.get_feature_view("USER_RECENT_ACTIVITY", V)
    df = fs.read_feature_view(fv, keys=[["1"]], store_type="online")
    df.show()


def cmd_reset(fs: FeatureStore) -> None:
    """Delete feature group + all feature views + stream source for a clean rebuild
    (keeps the running online service and the entities)."""
    try:
        fs.delete_feature_group("PLAYER_REC_FG", "V1")
        print("[fs] deleted feature group")
    except Exception as exc:  # noqa: BLE001
        print(f"[fs] feature group delete: {exc}")
    for fv in ("USER_CONTEXT_ACTIVITY", "USER_RECENT_ACTIVITY", "USER_CATEGORY_RECENT",
               "GAME_PROFILE_FV", "PLAYER_AFFINITY_FV", "PLAYER_BEHAVIOR_FV"):
        try:
            fs.delete_feature_view(fv, V)
            print(f"[fs] deleted {fv}")
        except Exception as exc:  # noqa: BLE001
            print(f"[fs] delete {fv}: {exc}")
    try:
        fs.delete_stream_source(STREAM_SOURCE)
        print("[fs] deleted stream source")
    except Exception as exc:  # noqa: BLE001
        print(f"[fs] stream source delete: {exc}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["all", "online", "register", "status", "ingest-test", "reset", "teardown"])
    args = ap.parse_args()
    session = get_session()
    try:
        fs = open_fs(session)
        if args.step in ("all", "online", "register"):
            player, game = register_entities(fs)
        if args.step in ("all", "online"):
            # Online service MUST exist before registering online-enabled FVs.
            create_online(fs)
        if args.step in ("all", "register"):
            register_batch_fvs(fs, session, player, game)
            register_stream_fvs(fs, session, player)
            register_realtime_fv(fs, player)
            register_feature_group(fs)
            print("[fs] feature views + group registered")
        if args.step == "all":
            print("[fs] DONE: online service running + all feature views + group registered")
        if args.step == "status":
            cmd_status(fs)
        if args.step == "ingest-test":
            cmd_ingest_test(fs)
        if args.step == "reset":
            cmd_reset(fs)
        if args.step == "teardown":
            try:
                fs.drop_online_service()
                print("[fs] online service dropped")
            except Exception as exc:  # noqa: BLE001
                print(f"[fs] drop_online_service: {exc}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
