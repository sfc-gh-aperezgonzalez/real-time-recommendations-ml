-- =============================================================================
-- PlayNova ML Observability: ranker inference log + baseline + Model Monitor
-- Inference runs in the PLAYNOVA_RANKER_SVC SPCS service; the orchestrator
-- appends every served prediction to RANKER_INFERENCE_LOG. A model version
-- monitor over that table surfaces drift / volume / statistical metrics in
-- Model Registry > Monitors, segmented by region.
--
-- Order of operations (see deploy):
--   1. Run the two CREATE TABLE statements below.
--   2. Deploy PLAYNOVA_RANKER_SVC and populate RANKER_BASELINE with REAL rows
--      scored by that service (ml/backfill_inference.py).
--   3. Run CREATE MODEL MONITOR last -- it embeds a snapshot of the baseline at
--      creation time, so the baseline must be populated first.
-- =============================================================================
USE ROLE ACCOUNTADMIN;
USE DATABASE PLAYNOVA_RECS_DEMO;
USE SCHEMA ML;

-- Monitored inference log: one row per (player, candidate) prediction served.
-- Columns map to the ranker signature; SCORE is the served probability [0,1].
CREATE TABLE IF NOT EXISTS PLAYNOVA_RECS_DEMO.ML.RANKER_INFERENCE_LOG (
    ROW_ID                   STRING          NOT NULL,   -- unique row id (monitor ID column)
    EVENT_TS                 TIMESTAMP_NTZ   NOT NULL,   -- prediction time (monitor timestamp)
    AFF_FOR_CATEGORY         FLOAT           NOT NULL,   -- feature
    GAME_ROUNDS_30D_NORM     FLOAT           NOT NULL,   -- feature
    POPULARITY_TREND         FLOAT           NOT NULL,   -- feature
    RTP_FRAC                 FLOAT           NOT NULL,   -- feature
    PLAYER_ROUNDS_30D        FLOAT           NOT NULL,   -- feature
    RECENT_CAT_ACTIVITY_NORM FLOAT           NOT NULL,   -- feature (real-time recency, V2)
    SCORE                    FLOAT           NOT NULL,   -- prediction score (0..1)
    REGION_CODE              STRING          NOT NULL    -- segment column
);

-- Reference/baseline snapshot (real rows scored by the same service) used for
-- drift. Same monitored columns as the source; no id/timestamp required.
CREATE TABLE IF NOT EXISTS PLAYNOVA_RECS_DEMO.ML.RANKER_BASELINE (
    AFF_FOR_CATEGORY         FLOAT           NOT NULL,
    GAME_ROUNDS_30D_NORM     FLOAT           NOT NULL,
    POPULARITY_TREND         FLOAT           NOT NULL,
    RTP_FRAC                 FLOAT           NOT NULL,
    PLAYER_ROUNDS_30D        FLOAT           NOT NULL,
    RECENT_CAT_ACTIVITY_NORM FLOAT           NOT NULL,
    SCORE                    FLOAT           NOT NULL,
    REGION_CODE              STRING          NOT NULL
);

-- ---------------------------------------------------------------------------
-- Model version monitor.
-- Created by ml/backfill_inference.py --create-monitor AFTER RANKER_BASELINE is
-- populated (the monitor embeds a baseline snapshot at creation time). The
-- canonical DDL lives there; it is reproduced here for reference only.
--
--   CREATE OR REPLACE MODEL MONITOR PLAYNOVA_RECS_DEMO.ML.PLAYNOVA_RANKER_MONITOR WITH
--       MODEL = PLAYNOVA_RECS_DEMO.ML.PLAYNOVA_RANKER
--       VERSION = 'V2'  FUNCTION = 'predict'
--       SOURCE = PLAYNOVA_RECS_DEMO.ML.RANKER_INFERENCE_LOG
--       BASELINE = PLAYNOVA_RECS_DEMO.ML.RANKER_BASELINE
--       WAREHOUSE = COMPUTE_WH  REFRESH_INTERVAL = '1 hour'  AGGREGATION_WINDOW = '1 day'
--       TIMESTAMP_COLUMN = EVENT_TS  ID_COLUMNS = ( 'ROW_ID' )
--       PREDICTION_SCORE_COLUMNS = ( 'SCORE' )  SEGMENT_COLUMNS = ( 'REGION_CODE' );
