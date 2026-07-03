"""PlayNova Recommendation Orchestrator (SPCS, FastAPI).

The single backend entry point the web app calls. Owns:
  - user/market/context resolution
  - real-time feature signal from the Online Feature Store (recent-category plays)
  - deterministic business-rule filtering BEFORE scoring (market availability,
    player category/subvertical exclusions)
  - eligible candidate-set construction + ranker feature assembly (one Snowflake query)
  - ranking via the Snowflake Model Registry inference service (Snowflake-served; no local fallback)
  - top-N selection with diversity, rail assembly
  - persistence of recommendation output + full observability trace
  - gameplay event ingestion (raw write + OFS stream ingest)

Runs both locally (connections.toml 'default') and in SPCS (OAuth token file).
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from snow import get_session, run_query  # local module

DEMO_DB = os.environ.get("PLAYNOVA_DB", "PLAYNOVA_RECS_DEMO")
OFS_QUERY_URL = os.environ.get("OFS_QUERY_URL", "").rstrip("/")
SNOWFLAKE_PAT = os.environ.get("SNOWFLAKE_PAT", "")
MODEL_VERSION = os.environ.get("MODEL_VERSION", "ranker-v2")
RANKER_SERVICE = os.environ.get("RANKER_SERVICE", "PLAYNOVA_RANKER_SVC")
RANKER_SERVICE_SCHEMA = os.environ.get("RANKER_SERVICE_SCHEMA", "ML")
RANKER_INFERENCE_URL = os.environ.get("RANKER_INFERENCE_URL", "").rstrip("/")

app = FastAPI(title="PlayNova Recommendation Orchestrator", version="1.0")

# Ranker feature columns, in the order of the registered PLAYNOVA_RANKER signature.
FEATURE_COLS = ["AFF_FOR_CATEGORY", "GAME_ROUNDS_30D_NORM", "POPULARITY_TREND", "RTP_FRAC", "PLAYER_ROUNDS_30D", "RECENT_CAT_ACTIVITY_NORM"]
# Real-time recency feature normalization cap. MUST match ml/train.py RECENCY_CAP
# so the served model receives the feature normalized exactly as it was trained
# (training-serving parity). RECENT_CAT_ACTIVITY_NORM = min(cat_plays_24h/CAP, 1.0).
RECENCY_CAP = 10
_REGION_RE = re.compile(r"^[A-Za-z]{2,5}$")
_INFERENCE = None  # cached (base_url, auth_mode) for the model inference service


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class RecRequest(BaseModel):
    player_id: int
    region_code: Optional[str] = None
    page_context: Optional[str] = "home"
    because_you_played: Optional[int] = None
    top_n: int = 12


class GameEvent(BaseModel):
    player_id: int
    event_type: str  # REGISTER / LOGIN / SESSION_START / PLAY
    game_title_id: Optional[int] = None
    category_id: Optional[int] = None
    region_code: Optional[str] = None
    stake_amt: Optional[float] = 0.0
    session_id: Optional[str] = None


# --------------------------------------------------------------------------- #
# Model inference service client (PLAYNOVA_RANKER served on SPCS by the registry)
# --------------------------------------------------------------------------- #
def _read_spcs_token() -> str:
    try:
        with open("/snowflake/session/token") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return ""


def get_inference_endpoint():
    """Resolve the ranker inference service endpoint once.

    In SPCS we call the service over the internal service-mesh DNS
    (http://<dns>:<port>/predict-proba) with the container OAuth token, since
    internet egress is disabled. Locally, set RANKER_INFERENCE_URL (public
    ingress) and authenticate with the PAT. Only successful resolutions are
    cached, so a not-yet-ready service is retried on the next request.
    """
    global _INFERENCE
    if _INFERENCE is not None:
        return _INFERENCE
    if RANKER_INFERENCE_URL:
        _INFERENCE = (RANKER_INFERENCE_URL, "pat")
        return _INFERENCE
    fqsvc = f"{DEMO_DB}.{RANKER_SERVICE_SCHEMA}.{RANKER_SERVICE}"
    try:
        eps = run_query(f"SHOW ENDPOINTS IN SERVICE {fqsvc}")
        row = next((e for e in eps if str(e.get("name", "")).lower() == "inference"), eps[0] if eps else {})
        port = row.get("port") or 5000
        desc = run_query(f"DESCRIBE SERVICE {fqsvc}")
        dns = desc[0].get("dns_name") if desc else None
        if dns:
            resolved = (f"http://{dns}:{port}", "oauth")
            _INFERENCE = resolved
            print(f"[orch] ranker inference endpoint: {resolved[0]}")
            return resolved
        print("[orch] ranker service has no dns_name yet; request will 503 until it is READY")
    except Exception as exc:  # noqa: BLE001
        print(f"[orch] could not resolve ranker inference endpoint ({exc}); request will 503")
    return ("", "")


def _parse_proba(data: Any, n: int) -> Optional[list[float]]:
    """Extract P(class=1) per row from the model service response.

    Response shape: {"data": [[row_index, {"output_feature_0": p0,
    "output_feature_1": p1}], ...]}. output_feature_1 is P(class=1).
    """
    rows = data.get("data") if isinstance(data, dict) else (data if isinstance(data, list) else None)
    if not isinstance(rows, list) or not rows:
        return None
    out: list[float] = []
    for row in rows:
        rec: Any = None
        if isinstance(row, (list, tuple)):
            rec = row[-1] if row and isinstance(row[-1], dict) else None
            if rec is None:  # fallback: [idx, p0, p1] -> last numeric is P(class=1)
                nums = [x for x in row if isinstance(x, (int, float)) and not isinstance(x, bool)]
                out.append(float(nums[-1]) if len(nums) >= 2 else None)
                continue
        elif isinstance(row, dict):
            rec = row
        if not isinstance(rec, dict):
            return None
        val = rec.get("output_feature_1")
        if val is None:
            probs = [v for k, v in rec.items() if str(k).startswith("output_feature")]
            val = probs[-1] if probs else None
        out.append(float(val) if val is not None else None)
    if len(out) != n or any(v is None for v in out):
        return None
    return out


def predict_scores(candidates: list[dict]) -> Optional[list[float]]:
    """Call the served ranker's predict_proba. Returns P(class=1) per candidate,
    or None if the service is unavailable (caller raises RankerUnavailable -> 503)."""
    if not candidates:
        return []
    base, mode = get_inference_endpoint()
    if not base:
        return None
    token = _read_spcs_token() if mode == "oauth" else SNOWFLAKE_PAT
    if not token:
        return None
    payload = {"dataframe_split": {
        "index": list(range(len(candidates))),
        "columns": FEATURE_COLS,
        "data": [[float(c.get(k) or 0.0) for k in FEATURE_COLS] for c in candidates],
    }}
    headers = {"Authorization": f'Snowflake Token="{token}"', "Content-Type": "application/json"}
    try:
        r = requests.post(f"{base}/predict-proba", headers=headers, json=payload, timeout=8)
        r.raise_for_status()
        return _parse_proba(r.json(), len(candidates))
    except Exception as exc:  # noqa: BLE001
        print(f"[orch] inference service call failed ({exc}); request will 503")
        return None


# --------------------------------------------------------------------------- #
# Online feature retrieval (OFS REST query API, with SQL fallback)
# --------------------------------------------------------------------------- #
_OFS_QUERY_URL = None


def get_ofs_query_url() -> str:
    """Resolve the Online Service query endpoint once (env override, else SDK)."""
    global _OFS_QUERY_URL
    if _OFS_QUERY_URL is not None:
        return _OFS_QUERY_URL
    if OFS_QUERY_URL:
        _OFS_QUERY_URL = OFS_QUERY_URL.rstrip("/")
        return _OFS_QUERY_URL
    try:
        from snowflake.ml.feature_store import CreationMode, FeatureStore, online_service
        fs = FeatureStore(session=get_session(), database=DEMO_DB, name="FEATURES",
                          default_warehouse=os.environ.get("PLAYNOVA_WH", "COMPUTE_WH"),
                          creation_mode=CreationMode.FAIL_IF_NOT_EXIST)
        st = fs.get_online_service_status()
        _OFS_QUERY_URL = (online_service.endpoint_url(st, "query") or "").rstrip("/")
    except Exception as exc:  # noqa: BLE001
        print(f"[orch] could not resolve OFS query url: {exc}")
        _OFS_QUERY_URL = ""
    return _OFS_QUERY_URL


def ofs_query(fv_name: str, version: str, entity: dict, features: Optional[list[str]] = None) -> dict:
    """Query one online feature view for one entity. Returns {feature_name: value}.

    Parses the OFS Query API response: results[0].features aligns to
    metadata.features[].name. Returns {} on any failure (caller falls back to SQL).
    """
    url = get_ofs_query_url()
    if not (url and SNOWFLAKE_PAT):
        return {}
    body: dict[str, Any] = {
        "name": fv_name, "version": version, "object_type": "feature_view",
        "metadata_options": {"include_names": True, "include_data_types": True},
        "request_rows": [{"entity": entity}],
    }
    if features:
        body["features"] = features
    try:
        r = requests.post(
            f"{url}/api/v1/query",
            headers={"Authorization": f'Snowflake Token="{SNOWFLAKE_PAT}"', "Content-Type": "application/json"},
            json=body, timeout=5,
        )
        r.raise_for_status()
        data = r.json()
        names = [f["name"] for f in data.get("metadata", {}).get("features", [])]
        results = data.get("results") or []
        if not results or not names:
            return {}
        vals = results[0].get("features", [])
        return dict(zip(names, vals))
    except Exception as exc:  # noqa: BLE001
        print(f"[orch] OFS query {fv_name} failed: {exc}")
        return {}


def get_recent_categories(player_id: int) -> tuple[dict[int, float], str]:
    """Per-category recent (24h) play counts from the Online Feature Store stream FV
    (USER_CATEGORY_RECENT, secondary-keyed by CATEGORY_ID). SQL fallback on RAW.
    Returns (counts_by_category, source) where source is 'online' or 'sql'."""
    res = ofs_query("USER_CATEGORY_RECENT", "V1", {"PLAYER_ID": player_id})
    keys = res.get("CATEGORY_ID_KEYS_24H")
    plays = res.get("CAT_PLAYS_24H")
    if isinstance(keys, list) and isinstance(plays, list) and keys:
        return {int(k): float(p) for k, p in zip(keys, plays)}, "online"
    # Fallback: compute directly from the raw event stream (immediate freshness).
    rows = run_query(f"""
        SELECT CATEGORY_ID, COUNT(*) C FROM {DEMO_DB}.RAW.GAMEPLAY_EVENTS
        WHERE PLAYER_ID = {player_id} AND EVENT_TYPE = 'PLAY'
          AND EVENT_TS >= DATEADD(hour, -24, CURRENT_TIMESTAMP()) AND CATEGORY_ID IS NOT NULL
        GROUP BY CATEGORY_ID""")
    return {int(r["CATEGORY_ID"]): float(r["C"]) for r in rows}, "sql"


_FS = None
_STREAM_SRC = None


def _build_stream_source():
    global _STREAM_SRC
    if _STREAM_SRC is not None:
        return _STREAM_SRC
    from snowflake.ml.feature_store import StreamSource
    from snowflake.snowpark.types import (
        DoubleType, LongType, StringType, StructField, StructType, TimestampTimeZone, TimestampType,
    )
    _STREAM_SRC = StreamSource(
        name="PLAYNOVA_GAMEPLAY_EVENTS",
        schema=StructType([
            StructField("PLAYER_ID", LongType()),
            StructField("EVENT_TS", TimestampType(TimestampTimeZone.NTZ)),
            StructField("EVENT_TYPE", StringType()),
            StructField("GAME_TITLE_ID", LongType()),
            StructField("CATEGORY_ID", LongType()),
            StructField("REGION_CODE", StringType()),
            StructField("STAKE_AMT", DoubleType()),
            StructField("SESSION_ID", StringType()),
            StructField("GAME_KEY", StringType()),
        ]),
        desc="Real-time PlayNova gameplay events (mirrors RAW.GAMEPLAY_EVENTS)",
    )
    return _STREAM_SRC


def ofs_stream_ingest(record: dict) -> int:
    """Ingest one PLAY event into the Online Feature Store stream source.

    Passes the StreamSource OBJECT (not the name) to avoid the SDK's cross-session
    get_stream_source lookup. Requires SNOWFLAKE_PAT.
    """
    global _FS
    from snowflake.ml.feature_store import CreationMode, FeatureStore
    if _FS is None:
        _FS = FeatureStore(session=get_session(), database=DEMO_DB, name="FEATURES",
                           default_warehouse=os.environ.get("PLAYNOVA_WH", "COMPUTE_WH"),
                           creation_mode=CreationMode.FAIL_IF_NOT_EXIST)
    return _FS.stream_ingest(_build_stream_source(), [record])


# --------------------------------------------------------------------------- #
# Candidate construction + policy enforcement + ranker feature assembly.
# One Snowflake query does eligibility, player exclusions, and the 5 ranker
# features (Snowflake does the heavy lifting; the orchestrator stays thin).
# --------------------------------------------------------------------------- #
def assemble_candidates(player_id: int, region_code: str) -> tuple[list[dict], list[dict], dict]:
    """Returns (eligible_candidates, excluded[{game,reason}], rules_applied).

    Each candidate row already carries the ranker feature columns
    (AFF_FOR_CATEGORY, GAME_ROUNDS_30D_NORM, POPULARITY_TREND, RTP_FRAC,
    PLAYER_ROUNDS_30D) computed in SQL. Market eligibility is enforced LIVE
    against the policy tables (not the 1-hour-lag MARKET_ELIGIBLE_GAMES dynamic
    table) so Streamlit policy edits take effect on the very next request.
    """
    pid = int(player_id)
    region = region_code if _REGION_RE.match(region_code or "") else "UK"
    rows = run_query(f"""
        WITH aff AS (
            SELECT * FROM {DEMO_DB}.FEATURES.PLAYER_AFFINITY_PROFILE WHERE PLAYER_ID = {pid}
        ), beh AS (
            SELECT ROUNDS_30D FROM {DEMO_DB}.FEATURES.PLAYER_BEHAVIOR_PROFILE WHERE PLAYER_ID = {pid}
        )
        SELECT g.GAME_TITLE_ID, g.CATEGORY_ID, c.SUBVERTICAL,
               g.GAME_TITLE, g.STUDIO_NAME, g.TILE_IMAGE_URL, g.TILE_COLOR_HEX,
               c.VERTICAL, c.CATEGORY_NAME,
               ZEROIFNULL(p.ROUNDS_30D) AS ROUNDS_30D,
               ZEROIFNULL(p.GGR_30D_EUR) AS GGR_30D_EUR,
               ZEROIFNULL(p.POPULARITY_TREND) AS POPULARITY_TREND,
               ZEROIFNULL(g.RETURN_TO_PLAYER_PCT) AS RTP,
               -- ranker features (assembled in Snowflake, matching the model signature)
               COALESCE(CASE g.CATEGORY_ID
                   WHEN 1 THEN aff.AFF_SLOTS WHEN 2 THEN aff.AFF_JACKPOT WHEN 3 THEN aff.AFF_CLASSIC
                   WHEN 4 THEN aff.AFF_TABLE WHEN 5 THEN aff.AFF_SCRATCH WHEN 6 THEN aff.AFF_LIVE_ROULETTE
                   WHEN 7 THEN aff.AFF_LIVE_BLACKJACK WHEN 8 THEN aff.AFF_LIVE_BACCARAT WHEN 9 THEN aff.AFF_GAME_SHOW
                   WHEN 10 THEN aff.AFF_SPORTSBOOK WHEN 11 THEN aff.AFF_ESPORTS WHEN 12 THEN aff.AFF_MEGAWAYS
                   ELSE aff.AFF_SLOTS END, 0)                                  AS AFF_FOR_CATEGORY,
               ZEROIFNULL(p.ROUNDS_30D) / NULLIF(MAX(ZEROIFNULL(p.ROUNDS_30D)) OVER (), 0) AS GAME_ROUNDS_30D_NORM,
               ZEROIFNULL(g.RETURN_TO_PLAYER_PCT) / 100.0                      AS RTP_FRAC,
               COALESCE((SELECT ROUNDS_30D FROM beh), 0)                       AS PLAYER_ROUNDS_30D,
               -- Suppression reason (deterministic, BEFORE ML). Market rules take
               -- precedence over player rules so the audit trail is unambiguous.
               CASE WHEN blk.GAME_TITLE_ID IS NOT NULL THEN 'MARKET_GAME_BLOCK'
                    WHEN mce.CATEGORY_ID IS NOT NULL THEN 'MARKET_CATEGORY_EXCLUSION'
                    WHEN pce.CATEGORY_ID IS NOT NULL THEN 'PLAYER_CATEGORY_EXCLUSION'
                    WHEN pse.SUBVERTICAL IS NOT NULL THEN 'PLAYER_SUBVERTICAL_EXCLUSION'
                    ELSE NULL END                                             AS EXCL_REASON
        FROM {DEMO_DB}.CORE.GAME_TITLE_DIM g
        JOIN {DEMO_DB}.CORE.GAME_CATEGORY_DIM c ON c.CATEGORY_ID = g.CATEGORY_ID
        LEFT JOIN {DEMO_DB}.FEATURES.GAME_CATALOG_PROFILE p ON p.GAME_TITLE_ID = g.GAME_TITLE_ID
        LEFT JOIN aff ON TRUE
        LEFT JOIN {DEMO_DB}.APP.MARKET_GAME_BLOCK blk
               ON blk.REGION_CODE = '{region}' AND blk.GAME_TITLE_ID = g.GAME_TITLE_ID
        LEFT JOIN {DEMO_DB}.APP.MARKET_CATEGORY_EXCLUSION mce
               ON mce.REGION_CODE = '{region}' AND mce.CATEGORY_ID = g.CATEGORY_ID
        LEFT JOIN {DEMO_DB}.APP.PLAYER_CATEGORY_EXCLUSION pce
               ON pce.PLAYER_ID = {pid} AND pce.CATEGORY_ID = g.CATEGORY_ID
        LEFT JOIN {DEMO_DB}.APP.PLAYER_SUBVERTICAL_EXCLUSION pse
               ON pse.PLAYER_ID = {pid} AND pse.SUBVERTICAL = c.SUBVERTICAL
        WHERE g.AVAILABLE_FOR_PLAY_YN = TRUE
          AND (g.IS_GLOBAL_YN = TRUE OR g.HOME_REGION_CODE = '{region}')
    """)
    candidates, excluded = [], []
    for r in rows:
        if r["EXCL_REASON"]:
            # Every suppression (market OR player) is logged with its reason so the
            # trace + Telemetry page can answer "why did this player not see game X?"
            excluded.append({"game_title_id": r["GAME_TITLE_ID"], "reason": r["EXCL_REASON"]})
        else:
            candidates.append(r)

    rules = {
        "market_eligibility": True,
        "market_game_blocks": sorted({r["GAME_TITLE_ID"] for r in rows
                                      if r["EXCL_REASON"] == "MARKET_GAME_BLOCK"}),
        "market_category_exclusions": sorted({r["CATEGORY_ID"] for r in rows
                                              if r["EXCL_REASON"] == "MARKET_CATEGORY_EXCLUSION"}),
        "player_category_exclusions": sorted({r["CATEGORY_ID"] for r in rows
                                              if r["EXCL_REASON"] == "PLAYER_CATEGORY_EXCLUSION"}),
        "player_subvertical_exclusions": sorted({r["SUBVERTICAL"] for r in rows
                                                 if r["EXCL_REASON"] == "PLAYER_SUBVERTICAL_EXCLUSION"}),
    }
    return candidates, excluded, rules


# --------------------------------------------------------------------------- #
# Ranking (served by the Snowflake Model Registry inference service)
# --------------------------------------------------------------------------- #
class RankerUnavailable(RuntimeError):
    """Raised when the Snowflake-served ranker cannot score a request. Inference is
    Snowflake-served or the request fails - there is deliberately no local scoring
    path."""


def score_candidates(candidates: list[dict], recent_cats: dict[int, float] | None = None) -> None:
    """Score candidates with the served ranker (predict_proba -> P(class=1)).

    The real-time recency signal (recent 24h plays in the candidate's category,
    from the Online Feature Store) is set as the model input feature
    RECENT_CAT_ACTIVITY_NORM BEFORE inference, so the SERVED MODEL itself reacts to
    what the player just did - there is no post-hoc score boost. Sets c['SCORE']
    (the served prediction, ranked) and c['_MODEL_SCORE'] (same value, logged for
    ML Observability).

    Inference runs ONLY in Snowflake (PLAYNOVA_RANKER_SVC). If the service can't
    score, this raises RankerUnavailable - there is no Python heuristic fallback."""
    if not candidates:
        return
    recent_cats = recent_cats or {}
    # Real-time feature: normalize the live per-category recent-play count exactly
    # as ml/train.py did (min(count/CAP, 1.0)) and attach it to each candidate so
    # it flows into the model's feature vector (FEATURE_COLS).
    for c in candidates:
        cnt = float(recent_cats.get(c["CATEGORY_ID"], 0.0))
        c["RECENT_CAT_ACTIVITY_NORM"] = min(cnt / float(RECENCY_CAP), 1.0)
    preds = predict_scores(candidates)
    if preds is None:
        raise RankerUnavailable("ranker inference service unavailable")
    for c, base in zip(candidates, preds):
        c["_MODEL_SCORE"] = float(base)   # raw served prediction (logged for ML Observability)
        c["SCORE"] = float(base)          # ranked score IS the model output (no post-hoc boost)
        # "boosted" = this category had live recent activity that fed the model as a
        # real-time feature (so the model, not a heuristic, lifted it).
        c["_BOOSTED"] = recent_cats.get(c["CATEGORY_ID"], 0.0) > 0


def diversify(candidates: list[dict], top_n: int, max_per_cat: int = 4) -> list[dict]:
    ordered = sorted(candidates, key=lambda c: c["SCORE"], reverse=True)
    out, per_cat = [], {}
    for c in ordered:
        cat = c["CATEGORY_ID"]
        if per_cat.get(cat, 0) >= max_per_cat:
            continue
        per_cat[cat] = per_cat.get(cat, 0) + 1
        out.append(c)
        if len(out) >= top_n:
            break
    return out


def _card(c: dict, rail: str, trend_score: Optional[int] = None) -> dict:
    card = {
        "game_title_id": c["GAME_TITLE_ID"], "title": c["GAME_TITLE"], "studio": c["STUDIO_NAME"],
        "category": c["CATEGORY_NAME"], "category_id": c["CATEGORY_ID"], "vertical": c["VERTICAL"],
        "tile_url": c["TILE_IMAGE_URL"], "tile_color": c["TILE_COLOR_HEX"],
        "score": round(float(c.get("SCORE", 0)), 4),
        "model_score": round(float(c.get("_MODEL_SCORE", c.get("SCORE", 0))), 4),
        "boosted": bool(c.get("_BOOSTED", False)), "rail": rail,
    }
    if trend_score is not None:
        card["trend_score"] = trend_score
    return card


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def persist(player_id, region_code, page_context, rails, candidate_size, excluded, rules, latency):
    sess = get_session()
    trace_id = str(uuid.uuid4())
    top_n_payload = [{"rail": c["rail"], "game_title_id": c["game_title_id"], "score": c["score"]}
                     for cards in rails.values() for c in cards]
    rows = []
    for rail, cards in rails.items():
        for rank, c in enumerate(cards, 1):
            rows.append((str(uuid.uuid4()), player_id, region_code, rail, rank,
                         c["game_title_id"], float(c["score"]), MODEL_VERSION))
    if rows:
        sess.sql(
            f"INSERT INTO {DEMO_DB}.APP.RECOMMENDATION_OUTPUT "
            "(REC_ID,PLAYER_ID,REGION_CODE,RAIL,RANK,GAME_TITLE_ID,SCORE,MODEL_VERSION) VALUES " +
            ",".join(["(?,?,?,?,?,?,?,?)"] * len(rows)),
            params=[v for r in rows for v in r],
        ).collect()
    sess.sql(
        f"""INSERT INTO {DEMO_DB}.APP.RECOMMENDATION_TRACE
            (TRACE_ID,REQUEST_TS,PLAYER_ID,REGION_CODE,PAGE_CONTEXT,CANDIDATE_SET_SIZE,
             RULES_APPLIED,EXCLUDED_CANDIDATES,TOP_N,MODEL_VERSION,LATENCY_BREAKDOWN_MS)
            SELECT ?,CURRENT_TIMESTAMP(),?,?,?,?,PARSE_JSON(?),PARSE_JSON(?),PARSE_JSON(?),?,PARSE_JSON(?)""",
        params=[trace_id, player_id, region_code, page_context, candidate_size,
                json.dumps(rules), json.dumps(excluded[:200]), json.dumps(top_n_payload),
                MODEL_VERSION, json.dumps(latency)],
    ).collect()
    return trace_id


def log_inference(candidates: list[dict], region_code: str) -> int:
    """Append served ranker predictions to ML.RANKER_INFERENCE_LOG for ML
    Observability. SCORE is the raw served prediction P(class=1)."""
    scored = [c for c in candidates if "_MODEL_SCORE" in c]
    if not scored:
        return 0
    import datetime as _dt
    ts = _dt.datetime.utcnow()
    rows = [(str(uuid.uuid4()), ts,
             float(c.get("AFF_FOR_CATEGORY") or 0.0), float(c.get("GAME_ROUNDS_30D_NORM") or 0.0),
             float(c.get("POPULARITY_TREND") or 0.0), float(c.get("RTP_FRAC") or 0.0),
             float(c.get("PLAYER_ROUNDS_30D") or 0.0), float(c.get("RECENT_CAT_ACTIVITY_NORM") or 0.0),
             float(c["_MODEL_SCORE"]), region_code)
            for c in scored]
    try:
        get_session().sql(
            f"INSERT INTO {DEMO_DB}.ML.RANKER_INFERENCE_LOG "
            "(ROW_ID,EVENT_TS,AFF_FOR_CATEGORY,GAME_ROUNDS_30D_NORM,POPULARITY_TREND,RTP_FRAC,"
            "PLAYER_ROUNDS_30D,RECENT_CAT_ACTIVITY_NORM,SCORE,REGION_CODE) VALUES "
            + ",".join(["(?,?,?,?,?,?,?,?,?,?)"] * len(rows)),
            params=[v for r in rows for v in r],
        ).collect()
    except Exception as exc:  # noqa: BLE001
        print(f"[orch] inference-log insert failed: {exc}")
    return len(rows)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok", "model_version": MODEL_VERSION,
            "ofs_online": bool(OFS_QUERY_URL and SNOWFLAKE_PAT),
            "inference_served": bool(_INFERENCE and _INFERENCE[0])}


@app.get("/telemetry")
def telemetry(player_id: int, limit: int = 8):
    """Recent recommendation traces for a player, read live from
    APP.RECOMMENDATION_TRACE. This is the same data the Snowsight worksheet shows:
    per-request latency breakdown across the Snowflake components, scoring path,
    feature source, model version, and policy exclusions (by reason)."""
    pid = int(player_id)
    n = max(1, min(int(limit), 25))
    rows = run_query(f"""
        SELECT TRACE_ID, REQUEST_TS, REGION_CODE, PAGE_CONTEXT, CANDIDATE_SET_SIZE,
               RULES_APPLIED:scoring::string        AS SCORING,
               RULES_APPLIED:feature_source::string AS FEATURE_SOURCE,
               MODEL_VERSION,
               LATENCY_BREAKDOWN_MS:resolve::float         AS L_RESOLVE,
               LATENCY_BREAKDOWN_MS:recent_activity::float AS L_RECENT_ACTIVITY,
               LATENCY_BREAKDOWN_MS:assemble::float        AS L_ASSEMBLE,
               LATENCY_BREAKDOWN_MS:rank::float            AS L_RANK,
               LATENCY_BREAKDOWN_MS:total::float           AS L_TOTAL,
               ARRAY_SIZE(EXCLUDED_CANDIDATES)             AS EXCLUDED_COUNT,
               EXCLUDED_CANDIDATES
        FROM {DEMO_DB}.APP.RECOMMENDATION_TRACE
        WHERE PLAYER_ID = {pid}
        ORDER BY REQUEST_TS DESC
        LIMIT {n}
    """)
    traces = []
    for r in rows:
        excl = r.get("EXCLUDED_CANDIDATES")
        if isinstance(excl, str):
            try:
                excl = json.loads(excl)
            except Exception:  # noqa: BLE001
                excl = []
        by_reason: dict[str, int] = {}
        for e in (excl or []):
            reason = (e or {}).get("reason", "UNKNOWN")
            by_reason[reason] = by_reason.get(reason, 0) + 1
        traces.append({
            "trace_id": r["TRACE_ID"],
            "request_ts": str(r["REQUEST_TS"]),
            "region_code": r["REGION_CODE"],
            "page_context": r["PAGE_CONTEXT"],
            "candidate_set_size": r["CANDIDATE_SET_SIZE"],
            "excluded_count": r.get("EXCLUDED_COUNT") or 0,
            "scoring": r.get("SCORING"),
            "feature_source": r.get("FEATURE_SOURCE"),
            "model_version": r.get("MODEL_VERSION"),
            "latency": {
                "resolve": r.get("L_RESOLVE"),
                "recent_activity": r.get("L_RECENT_ACTIVITY"),
                "assemble": r.get("L_ASSEMBLE"),
                "rank": r.get("L_RANK"),
                "total": r.get("L_TOTAL"),
            },
            "exclusions_by_reason": by_reason,
        })
    return {"player_id": pid, "traces": traces}


@app.post("/recommendations")
def recommendations(req: RecRequest):
    t = {}
    t0 = time.time()
    region = req.region_code
    if not region:
        r = run_query(f"""SELECT r.REGION_CODE FROM {DEMO_DB}.CORE.PLAYER_DIM p
                          JOIN {DEMO_DB}.CORE.REGION_DIM r ON r.REGION_ID=p.REGION_ID
                          WHERE p.PLAYER_ID={req.player_id}""")
        region = r[0]["REGION_CODE"] if r else "UK"
    t["resolve"] = round((time.time() - t0) * 1000, 1)

    t1b = time.time(); recent_cats, feat_source = get_recent_categories(req.player_id); t["recent_activity"] = round((time.time()-t1b)*1000, 1)
    t2 = time.time(); candidates, excluded, rules = assemble_candidates(req.player_id, region); t["assemble"] = round((time.time()-t2)*1000, 1)
    t3 = time.time()
    try:
        score_candidates(candidates, recent_cats)
    except RankerUnavailable as exc:
        # Snowflake-served inference or nothing: never fall back to local scoring.
        raise HTTPException(status_code=503, detail=f"Ranker inference service unavailable ({exc})")
    t["rank"] = round((time.time()-t3)*1000, 1)
    rules["feature_source"] = feat_source
    rules["scoring"] = "model"
    rules["recent_categories"] = recent_cats

    rec = diversify(candidates, req.top_n)
    trending = sorted(candidates, key=lambda c: c["ROUNDS_30D"] or 0, reverse=True)[: req.top_n]
    # Market-level popularity index (0-100) for the Trending rail badge, so it shows
    # "how hot in this market" rather than the per-player match score.
    max_trend = max((float(c["ROUNDS_30D"] or 0) for c in trending), default=0.0) or 1.0
    rails = {"recommended_for_you": [_card(c, "recommended_for_you") for c in rec],
             "trending_in_market": [_card(c, "trending_in_market",
                                          trend_score=round(100 * (float(c["ROUNDS_30D"] or 0) / max_trend)))
                                    for c in trending]}
    if req.because_you_played:
        played = run_query(f"SELECT CATEGORY_ID FROM {DEMO_DB}.CORE.GAME_TITLE_DIM WHERE GAME_TITLE_ID={req.because_you_played}")
        if played:
            cat = played[0]["CATEGORY_ID"]
            sim = [c for c in sorted(candidates, key=lambda c: c["SCORE"], reverse=True)
                   if c["CATEGORY_ID"] == cat][: req.top_n]
            rails["because_you_played"] = [_card(c, "because_you_played") for c in sim]

    t["total"] = round((time.time() - t0) * 1000, 1)
    trace_id = persist(req.player_id, region, req.page_context, rails, len(candidates), excluded, rules, t)
    log_inference(candidates, region)
    return {"player_id": req.player_id, "region_code": region, "trace_id": trace_id,
            "candidate_set_size": len(candidates), "excluded_count": len(excluded),
            "latency_ms": t, "rails": rails}


@app.post("/events")
def events(ev: GameEvent):
    sess = get_session()
    event_id = str(uuid.uuid4())
    region = ev.region_code
    if not region:
        r = run_query(f"""SELECT r.REGION_CODE FROM {DEMO_DB}.CORE.PLAYER_DIM p
                          JOIN {DEMO_DB}.CORE.REGION_DIM r ON r.REGION_ID=p.REGION_ID
                          WHERE p.PLAYER_ID={ev.player_id}""")
        region = r[0]["REGION_CODE"] if r else "UK"
    # 1. Raw event history (system of record)
    sess.sql(
        f"""INSERT INTO {DEMO_DB}.RAW.GAMEPLAY_EVENTS
            (EVENT_ID,PLAYER_ID,EVENT_TS,EVENT_TYPE,GAME_TITLE_ID,CATEGORY_ID,REGION_CODE,STAKE_AMT,SESSION_ID)
            VALUES (?,?,CURRENT_TIMESTAMP(),?,?,?,?,?,?)""",
        params=[event_id, ev.player_id, ev.event_type, ev.game_title_id, ev.category_id,
                region, ev.stake_amt or 0.0, ev.session_id or event_id],
    ).collect()
    # 2. OFS stream ingest (online feature update) - only meaningful for PLAY events
    ingested = 0
    if ev.event_type == "PLAY" and ev.game_title_id:
        try:
            ingested = ofs_stream_ingest({
                "PLAYER_ID": ev.player_id,
                "EVENT_TS": __import__("datetime").datetime.now(__import__("datetime").UTC).strftime("%Y-%m-%d %H:%M:%S"),
                "EVENT_TYPE": "PLAY", "GAME_TITLE_ID": ev.game_title_id,
                "CATEGORY_ID": ev.category_id or 1, "REGION_CODE": region,
                "STAKE_AMT": float(ev.stake_amt or 0.0), "SESSION_ID": ev.session_id or event_id,
                "GAME_KEY": str(ev.game_title_id),
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[orch] stream ingest failed: {exc}")
    return {"event_id": event_id, "raw_persisted": True, "ofs_ingested": ingested}


# --------------------------------------------------------------------------- #
# Auth + catalog endpoints (so the web app is a pure frontend)
# --------------------------------------------------------------------------- #
import hashlib  # noqa: E402


class RegisterRequest(BaseModel):
    email: str
    password: str
    region_code: str = "UK"
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


@app.get("/regions")
def regions():
    return {"regions": run_query(
        f"SELECT REGION_CODE, REGION_NAME FROM {DEMO_DB}.CORE.REGION_DIM WHERE IS_ACTIVE ORDER BY REGION_NAME")}


@app.post("/register")
def register(req: RegisterRequest):
    sess = get_session()
    existing = run_query(f"SELECT PLAYER_ID FROM {DEMO_DB}.APP.APP_CREDENTIAL WHERE EMAIL = '{req.email.replace(chr(39), '')}'")
    if existing:
        return {"error": "email already registered", "player_id": existing[0]["PLAYER_ID"]}
    region = run_query(f"SELECT REGION_ID FROM {DEMO_DB}.CORE.REGION_DIM WHERE REGION_CODE = '{req.region_code}'")
    region_id = region[0]["REGION_ID"] if region else 1
    new_id = run_query(f"SELECT COALESCE(MAX(PLAYER_ID),0)+1 AS NID FROM {DEMO_DB}.CORE.PLAYER_DIM")[0]["NID"]
    name = req.display_name or req.email.split("@")[0]
    sess.sql(
        f"""INSERT INTO {DEMO_DB}.CORE.PLAYER_DIM
            (PLAYER_ID, PLAYER_SOURCE_KEY, REGION_ID, RESIDENCE_COUNTRY_ID, CURRENCY_ID, PLAYER_SEGMENT_ID,
             LANGUAGE_ID, PLAYER_CODE, DISPLAY_NAME, EMAIL_HASH, PLAYER_SEGMENT, IS_TEST_PLAYER_YN,
             REGISTRATION_TIMESTAMP, IS_ACTIVE)
            SELECT ?, ?, ?, ?, 978, 4, 1, ?, ?, ?, 'CASUAL', TRUE, CURRENT_TIMESTAMP(), TRUE""",
        params=[new_id, f"ply_{new_id}", region_id, region_id, f"PC{new_id:07d}", name, _hash_pw(req.email)],
    ).collect()
    sess.sql(
        f"INSERT INTO {DEMO_DB}.APP.APP_CREDENTIAL (PLAYER_ID, EMAIL, PASSWORD_HASH) VALUES (?,?,?)",
        params=[new_id, req.email, _hash_pw(req.password)],
    ).collect()
    sess.sql(
        f"""INSERT INTO {DEMO_DB}.RAW.GAMEPLAY_EVENTS
            (EVENT_ID, PLAYER_ID, EVENT_TS, EVENT_TYPE, REGION_CODE, SESSION_ID)
            VALUES (?,?,CURRENT_TIMESTAMP(),'REGISTER',?,?)""",
        params=[str(uuid.uuid4()), new_id, req.region_code, str(uuid.uuid4())],
    ).collect()
    return {"player_id": new_id, "display_name": name, "region_code": req.region_code}


@app.post("/login")
def login(req: LoginRequest):
    rows = run_query(
        f"""SELECT c.PLAYER_ID, p.DISPLAY_NAME, r.REGION_CODE
            FROM {DEMO_DB}.APP.APP_CREDENTIAL c
            JOIN {DEMO_DB}.CORE.PLAYER_DIM p ON p.PLAYER_ID = c.PLAYER_ID
            JOIN {DEMO_DB}.CORE.REGION_DIM r ON r.REGION_ID = p.REGION_ID
            WHERE c.EMAIL = '{req.email.replace(chr(39), '')}' AND c.PASSWORD_HASH = '{_hash_pw(req.password)}'""")
    if not rows:
        return {"error": "invalid credentials"}
    return {"player_id": rows[0]["PLAYER_ID"], "display_name": rows[0]["DISPLAY_NAME"], "region_code": rows[0]["REGION_CODE"]}


@app.get("/demo-players")
def demo_players():
    """One-click demo login (no password). The three balanced LIVE_HIGH_ROLLER
    players (2694/2137/2261) are the real-time-loop protagonists: their Video Slots
    and Live Roulette affinities are ~equal, so playing Live Roulette visibly and
    honestly re-ranks recommendations toward it. They're listed first; the segment
    personas (player_000001 etc.) follow for variety."""
    rows = run_query(f"""
        SELECT p.PLAYER_ID, p.DISPLAY_NAME, p.PLAYER_SEGMENT, r.REGION_CODE
        FROM {DEMO_DB}.CORE.PLAYER_DIM p
        JOIN {DEMO_DB}.CORE.REGION_DIM r ON r.REGION_ID = p.REGION_ID
        WHERE p.PLAYER_ID IN (2694, 2137, 2261, 1, 2, 3, 5, 8)
        ORDER BY CASE WHEN p.PLAYER_ID IN (2694, 2137, 2261) THEN 0 ELSE 1 END, p.PLAYER_ID""")
    return {"players": rows}


@app.get("/game/{game_id}")
def game(game_id: int):
    rows = run_query(f"""
        SELECT g.GAME_TITLE_ID, g.GAME_TITLE, g.STUDIO_NAME, g.GAME_DESCRIPTION, g.TILE_IMAGE_URL,
               g.TILE_COLOR_HEX, g.RETURN_TO_PLAYER_PCT, c.CATEGORY_ID, c.CATEGORY_NAME, c.VERTICAL
        FROM {DEMO_DB}.CORE.GAME_TITLE_DIM g
        JOIN {DEMO_DB}.CORE.GAME_CATEGORY_DIM c ON c.CATEGORY_ID = g.CATEGORY_ID
        WHERE g.GAME_TITLE_ID = {game_id}""")
    return rows[0] if rows else {"error": "not found"}
