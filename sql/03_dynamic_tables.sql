-- ============================================================================
-- PlayNova Demo - Offline feature engineering (Dynamic Tables)
-- Gold-layer batch features for the recommendation engine. These feed both the
-- offline training set and the Online Feature Store batch feature views.
-- Run after 02_mock_data.sql.
-- ============================================================================
USE DATABASE PLAYNOVA_RECS_DEMO;
CREATE SCHEMA IF NOT EXISTS FEATURES COMMENT = 'Dynamic Tables + Feature Store';
USE SCHEMA FEATURES;
USE WAREHOUSE COMPUTE_WH;

-- ----------------------------------------------------------------------------
-- 1. Player behavior profile (slower-changing per-player features)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE DYNAMIC TABLE FEATURES.PLAYER_BEHAVIOR_PROFILE
  TARGET_LAG = '1 hour'
  WAREHOUSE = COMPUTE_WH
  REFRESH_MODE = AUTO
  INITIALIZE = ON_CREATE
AS
SELECT
    p.PLAYER_ID,
    p.PLAYER_SEGMENT,
    p.REGION_ID,
    r.REGION_CODE,
    COUNT(f.ROUND_SOURCE_KEY)                                              AS TOTAL_ROUNDS,
    COUNT(DISTINCT f.GAME_TITLE_ID)                                        AS DISTINCT_GAMES,
    COUNT_IF(f.ROUND_START_TIMESTAMP >= DATEADD(day, -7,  CURRENT_TIMESTAMP())) AS ROUNDS_7D,
    COUNT_IF(f.ROUND_START_TIMESTAMP >= DATEADD(day, -30, CURRENT_TIMESTAMP())) AS ROUNDS_30D,
    COUNT_IF(f.ROUND_START_TIMESTAMP >= DATEADD(day, -90, CURRENT_TIMESTAMP())) AS ROUNDS_90D,
    ZEROIFNULL(SUM(f.STAKE_TOTAL_AMT_EUR))                                 AS TOTAL_STAKE_EUR,
    ZEROIFNULL(AVG(f.STAKE_TOTAL_AMT_EUR))                                 AS AVG_STAKE_EUR,
    ZEROIFNULL(SUM(f.STAKE_TOTAL_AMT_EUR - f.PAYOUT_TOTAL_AMT_EUR))        AS TOTAL_GGR_EUR,
    MAX(f.ROUND_START_TIMESTAMP)                                           AS LAST_ACTIVITY_TS,
    DATEDIFF(day, MAX(f.ROUND_START_TIMESTAMP), CURRENT_TIMESTAMP())       AS DAYS_SINCE_LAST_PLAY,
    MODE(c.VERTICAL)                                                       AS PREF_VERTICAL,
    MODE(c.SUBVERTICAL)                                                    AS PREF_SUBVERTICAL
FROM CORE.PLAYER_DIM p
JOIN CORE.REGION_DIM r       ON r.REGION_ID = p.REGION_ID
LEFT JOIN CORE.GAME_ROUND_FACT f  ON f.PLAYER_ID = p.PLAYER_ID
LEFT JOIN CORE.GAME_TITLE_DIM g   ON g.GAME_TITLE_ID = f.GAME_TITLE_ID
LEFT JOIN CORE.GAME_CATEGORY_DIM c ON c.CATEGORY_ID = g.CATEGORY_ID
GROUP BY p.PLAYER_ID, p.PLAYER_SEGMENT, p.REGION_ID, r.REGION_CODE;

-- ----------------------------------------------------------------------------
-- 2. Player long-term affinity profile (recency-weighted category shares)
--    Wide layout (one row per player) for low-latency online serving.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE DYNAMIC TABLE FEATURES.PLAYER_AFFINITY_PROFILE
  TARGET_LAG = '1 hour'
  WAREHOUSE = COMPUTE_WH
  REFRESH_MODE = AUTO
  INITIALIZE = ON_CREATE
AS
WITH weighted AS (
    SELECT
        f.PLAYER_ID,
        c.CATEGORY_ID,
        c.SUBVERTICAL,
        GREATEST(0.05, 1.0 - DATEDIFF(day, f.ROUND_START_TIMESTAMP, CURRENT_TIMESTAMP()) / 180.0) AS W
    FROM CORE.GAME_ROUND_FACT f
    JOIN CORE.GAME_TITLE_DIM g    ON g.GAME_TITLE_ID = f.GAME_TITLE_ID
    JOIN CORE.GAME_CATEGORY_DIM c ON c.CATEGORY_ID = g.CATEGORY_ID
)
SELECT
    PLAYER_ID,
    SUM(W)                                                                          AS AFFINITY_WEIGHT_TOTAL,
    DIV0(SUM(IFF(CATEGORY_ID = 1,  W, 0)), SUM(W))                                  AS AFF_SLOTS,
    DIV0(SUM(IFF(CATEGORY_ID = 2,  W, 0)), SUM(W))                                  AS AFF_JACKPOT,
    DIV0(SUM(IFF(CATEGORY_ID = 3,  W, 0)), SUM(W))                                  AS AFF_CLASSIC,
    DIV0(SUM(IFF(CATEGORY_ID = 4,  W, 0)), SUM(W))                                  AS AFF_TABLE,
    DIV0(SUM(IFF(CATEGORY_ID = 5,  W, 0)), SUM(W))                                  AS AFF_SCRATCH,
    DIV0(SUM(IFF(CATEGORY_ID = 6,  W, 0)), SUM(W))                                  AS AFF_LIVE_ROULETTE,
    DIV0(SUM(IFF(CATEGORY_ID = 7,  W, 0)), SUM(W))                                  AS AFF_LIVE_BLACKJACK,
    DIV0(SUM(IFF(CATEGORY_ID = 8,  W, 0)), SUM(W))                                  AS AFF_LIVE_BACCARAT,
    DIV0(SUM(IFF(CATEGORY_ID = 9,  W, 0)), SUM(W))                                  AS AFF_GAME_SHOW,
    DIV0(SUM(IFF(CATEGORY_ID = 10, W, 0)), SUM(W))                                  AS AFF_SPORTSBOOK,
    DIV0(SUM(IFF(CATEGORY_ID = 11, W, 0)), SUM(W))                                  AS AFF_ESPORTS,
    DIV0(SUM(IFF(CATEGORY_ID = 12, W, 0)), SUM(W))                                  AS AFF_MEGAWAYS
FROM weighted
GROUP BY PLAYER_ID;

-- ----------------------------------------------------------------------------
-- 3. Game catalog profile (popularity / trend / GGR + catalog attributes)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE DYNAMIC TABLE FEATURES.GAME_CATALOG_PROFILE
  TARGET_LAG = '1 hour'
  WAREHOUSE = COMPUTE_WH
  REFRESH_MODE = AUTO
  INITIALIZE = ON_CREATE
AS
SELECT
    g.GAME_TITLE_ID,
    g.GAME_TITLE,
    g.CATEGORY_ID,
    c.VERTICAL,
    c.SUBVERTICAL,
    g.STUDIO_NAME,
    g.RETURN_TO_PLAYER_PCT,
    g.HAS_JACKPOT_YN,
    g.IS_GLOBAL_YN,
    g.HOME_REGION_CODE,
    g.TILE_IMAGE_URL,
    g.TILE_COLOR_HEX,
    ZEROIFNULL(COUNT_IF(f.ROUND_START_TIMESTAMP >= DATEADD(day, -30, CURRENT_TIMESTAMP())))      AS ROUNDS_30D,
    ZEROIFNULL(COUNT(DISTINCT IFF(f.ROUND_START_TIMESTAMP >= DATEADD(day, -30, CURRENT_TIMESTAMP()), f.PLAYER_ID, NULL))) AS PLAYERS_30D,
    ZEROIFNULL(COUNT_IF(f.ROUND_START_TIMESTAMP >= DATEADD(day, -60, CURRENT_TIMESTAMP())
                        AND f.ROUND_START_TIMESTAMP <  DATEADD(day, -30, CURRENT_TIMESTAMP())))  AS ROUNDS_PREV_30D,
    ZEROIFNULL(SUM(IFF(f.ROUND_START_TIMESTAMP >= DATEADD(day, -30, CURRENT_TIMESTAMP()),
                       f.STAKE_TOTAL_AMT_EUR - f.PAYOUT_TOTAL_AMT_EUR, 0)))                       AS GGR_30D_EUR,
    DIV0(COUNT_IF(f.ROUND_START_TIMESTAMP >= DATEADD(day, -30, CURRENT_TIMESTAMP())),
         COUNT_IF(f.ROUND_START_TIMESTAMP >= DATEADD(day, -60, CURRENT_TIMESTAMP())
                  AND f.ROUND_START_TIMESTAMP < DATEADD(day, -30, CURRENT_TIMESTAMP())) + 1)      AS POPULARITY_TREND,
    MAX(f.ROUND_START_TIMESTAMP)                                                                  AS LAST_PLAYED_TS
FROM CORE.GAME_TITLE_DIM g
JOIN CORE.GAME_CATEGORY_DIM c ON c.CATEGORY_ID = g.CATEGORY_ID
LEFT JOIN CORE.GAME_ROUND_FACT f ON f.GAME_TITLE_ID = g.GAME_TITLE_ID
GROUP BY g.GAME_TITLE_ID, g.GAME_TITLE, g.CATEGORY_ID, c.VERTICAL, c.SUBVERTICAL,
         g.STUDIO_NAME, g.RETURN_TO_PLAYER_PCT, g.HAS_JACKPOT_YN, g.IS_GLOBAL_YN,
         g.HOME_REGION_CODE, g.TILE_IMAGE_URL, g.TILE_COLOR_HEX;

-- ----------------------------------------------------------------------------
-- 4. Market-eligible game universe (catalog availability + policy enforcement)
--    Precompute used by the Streamlit "eligible catalog" preview and as a
--    deterministic baseline; the orchestrator re-checks policy live at request.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE DYNAMIC TABLE FEATURES.MARKET_ELIGIBLE_GAMES
  TARGET_LAG = '1 hour'
  WAREHOUSE = COMPUTE_WH
  REFRESH_MODE = AUTO
  INITIALIZE = ON_CREATE
AS
SELECT
    r.REGION_CODE,
    g.GAME_TITLE_ID,
    g.CATEGORY_ID,
    c.SUBVERTICAL,
    (
        g.AVAILABLE_FOR_PLAY_YN
        AND (g.IS_GLOBAL_YN OR g.HOME_REGION_CODE = r.REGION_CODE)
        AND blk.GAME_TITLE_ID IS NULL
        AND mce.CATEGORY_ID IS NULL
    )                                                                       AS IS_ELIGIBLE,
    CASE
        WHEN NOT g.AVAILABLE_FOR_PLAY_YN              THEN 'GAME_UNAVAILABLE'
        WHEN NOT (g.IS_GLOBAL_YN OR g.HOME_REGION_CODE = r.REGION_CODE) THEN 'NOT_IN_MARKET'
        WHEN blk.GAME_TITLE_ID IS NOT NULL            THEN 'MARKET_GAME_BLOCK'
        WHEN mce.CATEGORY_ID IS NOT NULL              THEN 'MARKET_CATEGORY_EXCLUSION'
        ELSE NULL
    END                                                                     AS INELIGIBLE_REASON
FROM CORE.REGION_DIM r
CROSS JOIN CORE.GAME_TITLE_DIM g
JOIN CORE.GAME_CATEGORY_DIM c ON c.CATEGORY_ID = g.CATEGORY_ID
LEFT JOIN APP.MARKET_GAME_BLOCK blk        ON blk.REGION_CODE = r.REGION_CODE AND blk.GAME_TITLE_ID = g.GAME_TITLE_ID
LEFT JOIN APP.MARKET_CATEGORY_EXCLUSION mce ON mce.REGION_CODE = r.REGION_CODE AND mce.CATEGORY_ID = g.CATEGORY_ID;

-- ----------------------------------------------------------------------------
-- 5. Player-game interaction base (positive labels for ranker training)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE DYNAMIC TABLE FEATURES.PLAYER_GAME_INTERACTION
  TARGET_LAG = '1 hour'
  WAREHOUSE = COMPUTE_WH
  REFRESH_MODE = AUTO
  INITIALIZE = ON_CREATE
AS
SELECT
    f.PLAYER_ID,
    f.GAME_TITLE_ID,
    g.CATEGORY_ID,
    COUNT(*)                          AS PLAY_COUNT,
    SUM(f.STAKE_TOTAL_AMT_EUR)        AS STAKE_EUR,
    MAX(f.ROUND_START_TIMESTAMP)      AS LAST_PLAY_TS,
    1                                 AS LABEL
FROM CORE.GAME_ROUND_FACT f
JOIN CORE.GAME_TITLE_DIM g ON g.GAME_TITLE_ID = f.GAME_TITLE_ID
GROUP BY f.PLAYER_ID, f.GAME_TITLE_ID, g.CATEGORY_ID;
