-- ============================================================================
-- PlayNova Real-Time Recommendations Demo - DDL
-- Namespace + game-recommendation schema subset (from spec/schema_mapping.md).
-- Net-zero: everything lives under PLAYNOVA_RECS_DEMO. The OFS online service
-- provisions its own dedicated managed Postgres (created in ml/feature_store.py),
-- so no external Postgres instance is referenced here.
-- Idempotent: safe to re-run.
-- ============================================================================

CREATE DATABASE IF NOT EXISTS PLAYNOVA_RECS_DEMO
  COMMENT = 'PlayNova real-time game recommendation demo';

USE DATABASE PLAYNOVA_RECS_DEMO;

CREATE SCHEMA IF NOT EXISTS CORE     COMMENT = 'Curated dimensions and facts';
CREATE SCHEMA IF NOT EXISTS RAW      COMMENT = 'Raw gameplay event history from the app';
CREATE SCHEMA IF NOT EXISTS FEATURES COMMENT = 'Dynamic Tables + Feature Store';
CREATE SCHEMA IF NOT EXISTS ML       COMMENT = 'Model registry + scored outputs';
CREATE SCHEMA IF NOT EXISTS APP      COMMENT = 'Policy rules, rec outputs, app config, image repo';

-- ----------------------------------------------------------------------------
-- CORE dimensions
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE CORE.REGION_DIM (
    REGION_ID             NUMBER        NOT NULL PRIMARY KEY,
    REGION_SOURCE_KEY     VARCHAR,
    REGION_CODE           VARCHAR       NOT NULL,            -- UK, ES, SE, DE, FI, NO, ...
    REGION_NAME           VARCHAR       NOT NULL,
    REGION_DESCRIPTION    VARCHAR,
    LICENSE_AUTHORITY_ID  NUMBER,
    IS_ACTIVE             BOOLEAN       DEFAULT TRUE
) COMMENT = 'Geographic/regulatory market (PlayNova REGION_DIM <- DIM_MARKET)';

CREATE OR REPLACE TABLE CORE.GAME_CATEGORY_DIM (
    CATEGORY_ID           NUMBER        NOT NULL PRIMARY KEY,
    CATEGORY_SOURCE_KEY   VARCHAR,
    CATEGORY_CODE         VARCHAR       NOT NULL,
    VERTICAL              VARCHAR       NOT NULL,            -- casino / live / sports
    SUBVERTICAL           VARCHAR       NOT NULL,            -- slots, jackpot, table, live_roulette, sportsbook, ...
    PRODUCT_DOMAIN        VARCHAR,
    CATEGORY_GROUP        VARCHAR,
    CATEGORY_NAME         VARCHAR       NOT NULL,
    IS_ACTIVE             BOOLEAN       DEFAULT TRUE
) COMMENT = 'Game category/vertical grouping (PlayNova GAME_CATEGORY_DIM <- DIM_GAME_EVENT_TYPE)';

CREATE OR REPLACE TABLE CORE.GAME_TITLE_DIM (
    GAME_TITLE_ID            NUMBER     NOT NULL PRIMARY KEY,
    GAME_TITLE_SOURCE_KEY    VARCHAR,
    CATEGORY_ID              NUMBER     NOT NULL,            -- FK -> GAME_CATEGORY_DIM
    GAME_CODE                VARCHAR,
    GAME_TITLE               VARCHAR    NOT NULL,
    GAME_SLUG                VARCHAR,
    GAME_DESCRIPTION         VARCHAR,
    STUDIO_NAME              VARCHAR,
    STUDIO_BRAND             VARCHAR,
    PLAY_ENVIRONMENT         VARCHAR,
    MIN_STAKE_AMT            NUMBER(18,2),
    MAX_STAKE_AMT            NUMBER(18,2),
    RETURN_TO_PLAYER_PCT     NUMBER(6,2),
    HAS_FREE_SPINS_YN        BOOLEAN    DEFAULT FALSE,
    HAS_JACKPOT_YN           BOOLEAN    DEFAULT FALSE,
    IS_PROGRESSIVE_JACKPOT_YN BOOLEAN   DEFAULT FALSE,
    AVAILABLE_FOR_PLAY_YN    BOOLEAN    DEFAULT TRUE,
    RELEASE_TIMESTAMP        TIMESTAMP_NTZ,
    IS_GLOBAL_YN             BOOLEAN    DEFAULT TRUE,         -- true = available all regions
    HOME_REGION_CODE         VARCHAR,                        -- set for region-restricted titles
    TILE_IMAGE_URL           VARCHAR,                        -- rail tile art (added)
    TILE_COLOR_HEX           VARCHAR,                        -- generated tile accent color (added)
    IS_ACTIVE                BOOLEAN    DEFAULT TRUE
) COMMENT = 'Game catalog (PlayNova GAME_TITLE_DIM <- DIM_GAME_EVENT) + tile art for the demo UI';

CREATE OR REPLACE TABLE CORE.PLAYER_DIM (
    PLAYER_ID                NUMBER     NOT NULL PRIMARY KEY,
    PLAYER_SOURCE_KEY        VARCHAR,
    REGION_ID                NUMBER     NOT NULL,            -- FK -> REGION_DIM
    RESIDENCE_COUNTRY_ID     NUMBER,
    CURRENCY_ID              NUMBER,
    PLAYER_SEGMENT_ID        NUMBER,
    LANGUAGE_ID              NUMBER,
    PLAYER_CODE              VARCHAR,
    DISPLAY_NAME             VARCHAR,                         -- synthetic, e.g. player_001923
    EMAIL_HASH               VARCHAR,                         -- sha2, non-PII
    AGE_BAND                 VARCHAR,
    ACQUISITION_CHANNEL      VARCHAR,
    PLAYER_SEGMENT           VARCHAR,                         -- SLOT_GRINDER / LIVE_HIGH_ROLLER / SPORTS_BETTOR / CASUAL
    IS_TEST_PLAYER_YN        BOOLEAN    DEFAULT FALSE,
    REGISTRATION_TIMESTAMP   TIMESTAMP_NTZ,
    FIRST_DEPOSIT_TIMESTAMP  TIMESTAMP_NTZ,
    IS_ACTIVE                BOOLEAN    DEFAULT TRUE
) COMMENT = 'Players (PlayNova PLAYER_DIM <- DIM_PLAYER); PII intentionally omitted';

-- ----------------------------------------------------------------------------
-- CORE fact: historical game rounds (drives affinity + training signal)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE CORE.GAME_ROUND_FACT (
    ROUND_SOURCE_KEY         VARCHAR    NOT NULL,
    PLAYER_ID                NUMBER     NOT NULL,            -- FK -> PLAYER_DIM
    GAME_TITLE_ID            NUMBER     NOT NULL,            -- FK -> GAME_TITLE_DIM
    SESSION_ID               NUMBER,
    CURRENCY_ID              NUMBER,
    STAKE_CASH_AMT           NUMBER(18,6),
    STAKE_CASH_AMT_EUR       NUMBER(18,6),
    STAKE_TOTAL_AMT          NUMBER(18,6),
    STAKE_TOTAL_AMT_EUR      NUMBER(18,6),
    PAYOUT_TOTAL_AMT_EUR     NUMBER(18,6),                   -- win/payout for GGR
    IS_ROUND_FINISHED_YN     BOOLEAN    DEFAULT TRUE,
    IS_FREESPIN_YN           BOOLEAN    DEFAULT FALSE,
    ROUND_START_TIMESTAMP    TIMESTAMP_NTZ NOT NULL,
    ROUND_END_TIMESTAMP      TIMESTAMP_NTZ,
    ROUND_DATE               DATE,
    IS_ACTIVE                BOOLEAN    DEFAULT TRUE
) COMMENT = 'Historical gameplay rounds (PlayNova GAME_ROUND_FACT <- FACT_GAME_ROUND)';

-- ----------------------------------------------------------------------------
-- RAW: live gameplay events written by the app (mirrors OFS StreamSource schema)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE RAW.GAMEPLAY_EVENTS (
    EVENT_ID                 VARCHAR    NOT NULL,
    PLAYER_ID                NUMBER     NOT NULL,
    EVENT_TS                 TIMESTAMP_NTZ NOT NULL,
    EVENT_TYPE               VARCHAR    NOT NULL,            -- REGISTER / LOGIN / SESSION_START / PLAY
    GAME_TITLE_ID            NUMBER,
    CATEGORY_ID              NUMBER,
    REGION_CODE              VARCHAR,
    STAKE_AMT                NUMBER(18,2),
    SESSION_ID               VARCHAR,
    INGESTED_AT              TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ
) COMMENT = 'System-of-record raw event history from the demo app; also fed to OFS stream source';

-- ----------------------------------------------------------------------------
-- APP: business-rule policy tables (managed by the Streamlit console)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE APP.MARKET_GAME_BLOCK (
    REGION_CODE              VARCHAR    NOT NULL,
    GAME_TITLE_ID            NUMBER     NOT NULL,
    REASON                   VARCHAR,
    UPDATED_AT               TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
    UPDATED_BY               VARCHAR,
    PRIMARY KEY (REGION_CODE, GAME_TITLE_ID)
) COMMENT = 'Explicit market->game availability blocks (overrides catalog availability)';

CREATE OR REPLACE TABLE APP.MARKET_CATEGORY_EXCLUSION (
    REGION_CODE              VARCHAR    NOT NULL,
    CATEGORY_ID              NUMBER     NOT NULL,
    REASON                   VARCHAR,
    UPDATED_AT               TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
    UPDATED_BY               VARCHAR,
    PRIMARY KEY (REGION_CODE, CATEGORY_ID)
) COMMENT = 'Market-level category exclusions';

CREATE OR REPLACE TABLE APP.PLAYER_CATEGORY_EXCLUSION (
    PLAYER_ID                NUMBER     NOT NULL,
    CATEGORY_ID              NUMBER     NOT NULL,
    REASON                   VARCHAR,
    UPDATED_AT               TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
    UPDATED_BY               VARCHAR,
    PRIMARY KEY (PLAYER_ID, CATEGORY_ID)
) COMMENT = 'Player-level category exclusions';

CREATE OR REPLACE TABLE APP.PLAYER_SUBVERTICAL_EXCLUSION (
    PLAYER_ID                NUMBER     NOT NULL,
    SUBVERTICAL              VARCHAR    NOT NULL,
    REASON                   VARCHAR,
    UPDATED_AT               TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
    UPDATED_BY               VARCHAR,
    PRIMARY KEY (PLAYER_ID, SUBVERTICAL)
) COMMENT = 'Player-level subvertical exclusions';

-- ----------------------------------------------------------------------------
-- APP: recommendation outputs + observability trace (spec section 11)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE APP.RECOMMENDATION_OUTPUT (
    REC_ID                   VARCHAR    NOT NULL,
    PLAYER_ID                NUMBER     NOT NULL,
    REGION_CODE              VARCHAR,
    RAIL                     VARCHAR,                         -- recommended_for_you / trending_in_market / because_you_played
    RANK                     NUMBER,
    GAME_TITLE_ID            NUMBER,
    SCORE                    FLOAT,
    MODEL_VERSION            VARCHAR,
    GENERATED_AT             TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ
) COMMENT = 'Persisted top-N recommendations per request for observability and replay';

CREATE OR REPLACE TABLE APP.RECOMMENDATION_TRACE (
    TRACE_ID                 VARCHAR    NOT NULL PRIMARY KEY,
    REQUEST_TS               TIMESTAMP_NTZ NOT NULL,
    PLAYER_ID                NUMBER,
    REGION_CODE              VARCHAR,
    PAGE_CONTEXT             VARCHAR,
    CANDIDATE_SET_SIZE       NUMBER,
    RULES_APPLIED            VARIANT,
    EXCLUDED_CANDIDATES      VARIANT,                         -- [{game_title_id, reason}]
    TOP_N                    VARIANT,
    MODEL_VERSION            VARCHAR,
    LATENCY_BREAKDOWN_MS     VARIANT
) COMMENT = 'Per-refresh recommendation trace: rules applied, exclusions w/ reason, latency breakdown';

-- ----------------------------------------------------------------------------
-- APP: demo-safe credentials for register/login (no real PII)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE APP.APP_CREDENTIAL (
    PLAYER_ID                NUMBER     NOT NULL PRIMARY KEY,
    EMAIL                    VARCHAR    NOT NULL,
    PASSWORD_HASH            VARCHAR    NOT NULL,             -- sha2 of demo password; never store plaintext
    CREATED_AT               TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ
) COMMENT = 'Demo-only credentials for the branded app (simulation, not production auth)';

-- Internal stage for generated game tile art served by the app.
CREATE STAGE IF NOT EXISTS APP.TILE_ASSETS
  DIRECTORY = (ENABLE = TRUE)
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
  COMMENT = 'Generated PlayNova game tile images';
