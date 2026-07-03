-- ============================================================================
-- PlayNova Demo - Mock data generation
-- Generates: 12 regions, 12 categories, 240+ games, 4000 players, ~1M historical
-- game rounds with segment->category affinity skew, and seeded policy rows.
-- Idempotent: truncates target tables first. Run after 01_ddl.sql.
-- ============================================================================
USE DATABASE PLAYNOVA_RECS_DEMO;
USE SCHEMA CORE;
USE WAREHOUSE COMPUTE_WH;

TRUNCATE TABLE IF EXISTS CORE.GAME_ROUND_FACT;
TRUNCATE TABLE IF EXISTS CORE.GAME_TITLE_DIM;
TRUNCATE TABLE IF EXISTS CORE.PLAYER_DIM;
TRUNCATE TABLE IF EXISTS CORE.GAME_CATEGORY_DIM;
TRUNCATE TABLE IF EXISTS CORE.REGION_DIM;

-- ----------------------------------------------------------------------------
-- Regions (12)
-- ----------------------------------------------------------------------------
INSERT INTO CORE.REGION_DIM (REGION_ID, REGION_SOURCE_KEY, REGION_CODE, REGION_NAME, REGION_DESCRIPTION, LICENSE_AUTHORITY_ID, IS_ACTIVE)
VALUES
 (1 ,'reg_uk','UK','United Kingdom','UKGC-licensed market',1,TRUE),
 (2 ,'reg_es','ES','Spain','DGOJ-licensed market',2,TRUE),
 (3 ,'reg_se','SE','Sweden','SGA-licensed market',3,TRUE),
 (4 ,'reg_de','DE','Germany','GGL-licensed market',4,TRUE),
 (5 ,'reg_fi','FI','Finland','Offshore market',5,TRUE),
 (6 ,'reg_no','NO','Norway','Offshore market',5,TRUE),
 (7 ,'reg_ca','CA','Canada (ON)','AGCO-licensed market',6,TRUE),
 (8 ,'reg_nz','NZ','New Zealand','Offshore market',5,TRUE),
 (9 ,'reg_ie','IE','Ireland','Offshore market',5,TRUE),
 (10,'reg_pt','PT','Portugal','SRIJ-licensed market',7,TRUE),
 (11,'reg_dk','DK','Denmark','Spillemyndigheden market',8,TRUE),
 (12,'reg_at','AT','Austria','Offshore market',5,TRUE);

-- ----------------------------------------------------------------------------
-- Categories (12) across 3 verticals
-- ----------------------------------------------------------------------------
INSERT INTO CORE.GAME_CATEGORY_DIM (CATEGORY_ID, CATEGORY_SOURCE_KEY, CATEGORY_CODE, VERTICAL, SUBVERTICAL, PRODUCT_DOMAIN, CATEGORY_GROUP, CATEGORY_NAME, IS_ACTIVE)
VALUES
 (1 ,'cat_slots'    ,'VIDEO_SLOTS'  ,'casino','slots'         ,'casino','slots' ,'Video Slots'      ,TRUE),
 (2 ,'cat_jackpot'  ,'JACKPOT_SLOTS','casino','jackpot'       ,'casino','slots' ,'Jackpot Slots'    ,TRUE),
 (3 ,'cat_classic'  ,'CLASSIC_SLOTS','casino','classic_slots' ,'casino','slots' ,'Classic Slots'    ,TRUE),
 (4 ,'cat_table'    ,'TABLE_GAMES'  ,'casino','table'         ,'casino','table' ,'Table Games'      ,TRUE),
 (5 ,'cat_scratch'  ,'SCRATCH'      ,'casino','scratch'       ,'casino','instant','Scratch & Instant',TRUE),
 (6 ,'cat_lroulette','LIVE_ROULETTE','live'  ,'live_roulette' ,'live'  ,'live'  ,'Live Roulette'    ,TRUE),
 (7 ,'cat_lblackjack','LIVE_BLACKJACK','live','live_blackjack','live'  ,'live'  ,'Live Blackjack'   ,TRUE),
 (8 ,'cat_lbaccarat','LIVE_BACCARAT','live'  ,'live_baccarat' ,'live'  ,'live'  ,'Live Baccarat'    ,TRUE),
 (9 ,'cat_gameshow' ,'GAME_SHOW'    ,'live'  ,'game_show'     ,'live'  ,'live'  ,'Live Game Shows'  ,TRUE),
 (10,'cat_sports'   ,'SPORTSBOOK'   ,'sports','sportsbook'    ,'sports','sports','Sportsbook'       ,TRUE),
 (11,'cat_esports'  ,'ESPORTS'      ,'sports','esports'       ,'sports','sports','Esports'          ,TRUE),
 (12,'cat_megaways' ,'MEGAWAYS'     ,'casino','megaways'      ,'casino','slots' ,'Megaways Slots'   ,TRUE);

-- ----------------------------------------------------------------------------
-- Games (240) - procedurally generated, casino-heavy mix, fictional studios
-- ----------------------------------------------------------------------------
INSERT INTO CORE.GAME_TITLE_DIM
SELECT
    1000 + g.i                                              AS GAME_TITLE_ID,
    'gt_' || (1000 + g.i)                                   AS GAME_TITLE_SOURCE_KEY,
    g.category_id                                           AS CATEGORY_ID,
    'G' || LPAD(g.i::STRING, 5, '0')                        AS GAME_CODE,
    g.game_title                                            AS GAME_TITLE,
    LOWER(REPLACE(g.game_title, ' ', '-')) || '-' || g.i    AS GAME_SLUG,
    'Spin up ' || g.game_title || ' - a ' || g.category_name || ' title by ' || g.studio AS GAME_DESCRIPTION,
    g.studio                                                AS STUDIO_NAME,
    g.studio                                                AS STUDIO_BRAND,
    CASE WHEN g.vertical = 'live' THEN 'LIVE' ELSE 'RNG' END AS PLAY_ENVIRONMENT,
    g.min_stake                                             AS MIN_STAKE_AMT,
    g.max_stake                                             AS MAX_STAKE_AMT,
    g.rtp                                                   AS RETURN_TO_PLAYER_PCT,
    (g.subvertical IN ('slots','megaways','classic_slots'))  AS HAS_FREE_SPINS_YN,
    (g.category_id = 2)                                     AS HAS_JACKPOT_YN,
    (g.category_id = 2 AND g.i % 2 = 0)                     AS IS_PROGRESSIVE_JACKPOT_YN,
    TRUE                                                    AS AVAILABLE_FOR_PLAY_YN,
    DATEADD(day, -UNIFORM(0, 1400, RANDOM()), CURRENT_TIMESTAMP())::TIMESTAMP_NTZ AS RELEASE_TIMESTAMP,
    g.is_global                                             AS IS_GLOBAL_YN,
    g.home_region                                           AS HOME_REGION_CODE,
    'tiles/game_' || (1000 + g.i) || '.svg'                 AS TILE_IMAGE_URL,
    g.tile_color                                            AS TILE_COLOR_HEX,
    TRUE                                                    AS IS_ACTIVE
FROM (
    SELECT
        r.i, r.category_id, r.vertical, r.subvertical, r.category_name,
        ARRAY_CONSTRUCT('NovaSpin Studios','Aurora Gaming','Vortex Play','Lumen Live','Pulse Interactive','Mirage Studios','Cobalt Games','Zenith Play','Halcyon Reels','Ironwood Gaming','Solstice Studios','Nebula Works')[ r.i % 12 ]::STRING AS studio,
        -- Globally-unique titles: casino uses casino-wide rank (theme+suffix);
        -- live/sports use per-category rank (theme + category name).
        CASE WHEN r.vertical = 'casino'
             THEN ARRAY_CONSTRUCT('Gold','Dragon','Fortune','Treasure','Aztec','Pirate','Mystic','Cosmic','Lucky','Phoenix','Diamond','Wild','Royal','Pharaoh','Viking','Samurai','Jungle','Ocean','Frost','Inferno','Neon','Crystal','Thunder','Gemstone','Safari','Carnival','Midas','Olympus','Tiki','Vault')[ MOD(r.rn_casino, 30) ]::STRING
                  || ' ' ||
                  ARRAY_CONSTRUCT('Riches','Bonanza','Megaways','Deluxe','Rush','Quest','Palace','Gold','Spins','Fortune','Strike','Legends','Kingdom','Power','Frenzy','Drop','Blitz','Jackpot','Reels','Empire')[ MOD(FLOOR(r.rn_casino / 30), 20) ]::STRING
             ELSE ARRAY_CONSTRUCT('Gold','Dragon','Fortune','Treasure','Aztec','Pirate','Mystic','Cosmic','Lucky','Phoenix','Diamond','Wild','Royal','Pharaoh','Viking','Samurai','Jungle','Ocean','Frost','Inferno','Neon','Crystal','Thunder','Gemstone','Safari','Carnival','Midas','Olympus','Tiki','Vault')[ MOD(r.rn_cat, 30) ]::STRING
                  || ' ' || r.category_name
        END AS game_title,
        CASE WHEN r.vertical='live' THEN 1.00 WHEN r.category_id=10 THEN 0.50 ELSE ARRAY_CONSTRUCT(0.10,0.20,0.25,0.50)[r.i % 4] END AS min_stake,
        CASE WHEN r.vertical='live' THEN ARRAY_CONSTRUCT(2000,5000,10000)[r.i % 3]
             WHEN r.category_id IN (10,11) THEN 5000
             ELSE ARRAY_CONSTRUCT(100,250,500,1000)[r.i % 4] END AS max_stake,
        ROUND(UNIFORM(9200, 9750, RANDOM()) / 100.0, 2) AS rtp,
        (UNIFORM(1, 100, RANDOM()) > 25) AS is_global,
        CASE WHEN (UNIFORM(1,100,RANDOM()) > 25) THEN NULL
             ELSE ARRAY_CONSTRUCT('UK','ES','SE','DE','PT','DK')[r.i % 6] END AS home_region,
        CASE r.vertical WHEN 'live' THEN '#0FB5A6' WHEN 'sports' THEN '#3BB54A'
             ELSE ARRAY_CONSTRUCT('#7A3FF2','#F2A03F','#F23F87','#3F7AF2')[r.i % 4] END AS tile_color
    FROM (
        SELECT cm.i, cm.category_id, cat.VERTICAL AS vertical, cat.SUBVERTICAL AS subvertical, cat.CATEGORY_NAME AS category_name,
               ROW_NUMBER() OVER (PARTITION BY cm.category_id ORDER BY cm.i) - 1 AS rn_cat,
               ROW_NUMBER() OVER (PARTITION BY (cat.VERTICAL = 'casino') ORDER BY cm.i) - 1 AS rn_casino
        FROM (
            SELECT i,
                CASE
                    WHEN m < 40 THEN 1   WHEN m < 55 THEN 2   WHEN m < 62 THEN 3
                    WHEN m < 70 THEN 12  WHEN m < 77 THEN 4   WHEN m < 80 THEN 5
                    WHEN m < 85 THEN 6   WHEN m < 89 THEN 7   WHEN m < 92 THEN 8
                    WHEN m < 95 THEN 9   WHEN m < 99 THEN 10  ELSE 11 END AS category_id
            FROM (SELECT SEQ4() AS i, MOD(SEQ4()*7 + 3, 100) AS m FROM TABLE(GENERATOR(ROWCOUNT => 240)))
        ) cm
        JOIN CORE.GAME_CATEGORY_DIM cat ON cat.CATEGORY_ID = cm.category_id
    ) r
) g;

-- ----------------------------------------------------------------------------
-- Players (4000) across 4 segments
-- ----------------------------------------------------------------------------
INSERT INTO CORE.PLAYER_DIM
SELECT
    p.i                                                     AS PLAYER_ID,
    'ply_' || p.i                                           AS PLAYER_SOURCE_KEY,
    p.region_id                                             AS REGION_ID,
    p.region_id                                             AS RESIDENCE_COUNTRY_ID,
    978                                                     AS CURRENCY_ID,            -- EUR
    p.segment_id                                            AS PLAYER_SEGMENT_ID,
    1                                                       AS LANGUAGE_ID,
    'PC' || LPAD(p.i::STRING, 7, '0')                       AS PLAYER_CODE,
    'player_' || LPAD(p.i::STRING, 6, '0')                  AS DISPLAY_NAME,
    SHA2('player_' || p.i || '@playnova.demo')              AS EMAIL_HASH,
    ARRAY_CONSTRUCT('18-24','25-34','35-44','45-54','55+')[p.i % 5] AS AGE_BAND,
    ARRAY_CONSTRUCT('SEO','PAID_SOCIAL','AFFILIATE','DIRECT','REFERRAL')[p.i % 5] AS ACQUISITION_CHANNEL,
    p.segment                                               AS PLAYER_SEGMENT,
    FALSE                                                   AS IS_TEST_PLAYER_YN,
    p.reg_ts                                                AS REGISTRATION_TIMESTAMP,
    DATEADD(day, UNIFORM(0, 5, RANDOM()), p.reg_ts)         AS FIRST_DEPOSIT_TIMESTAMP,
    TRUE                                                    AS IS_ACTIVE
FROM (
    SELECT
        SEQ4() + 1 AS i,
        -- region weighting: UK/SE/DE more common
        ARRAY_CONSTRUCT(1,1,1,3,3,4,4,2,5,6,7,10)[UNIFORM(0,11,RANDOM())] AS region_id,
        -- segment distribution ~45/20/20/15
        CASE
            WHEN s < 45 THEN 'SLOT_GRINDER'
            WHEN s < 65 THEN 'LIVE_HIGH_ROLLER'
            WHEN s < 85 THEN 'SPORTS_BETTOR'
            ELSE 'CASUAL' END AS segment,
        CASE
            WHEN s < 45 THEN 1 WHEN s < 65 THEN 2 WHEN s < 85 THEN 3 ELSE 4 END AS segment_id,
        DATEADD(day, -UNIFORM(0, 730, RANDOM()), CURRENT_TIMESTAMP())::TIMESTAMP_NTZ AS reg_ts
    FROM (SELECT SEQ4() AS dummy, UNIFORM(0,99,RANDOM()) AS s FROM TABLE(GENERATOR(ROWCOUNT => 4000)))
) p;

-- ----------------------------------------------------------------------------
-- Segment -> game sampling pool (transient helpers)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TRANSIENT TABLE CORE._SEG_CAT_WEIGHT (SEGMENT VARCHAR, CATEGORY_ID NUMBER, WEIGHT NUMBER) AS
SELECT * FROM VALUES
 ('SLOT_GRINDER',1,10),('SLOT_GRINDER',2,6),('SLOT_GRINDER',3,5),('SLOT_GRINDER',12,7),('SLOT_GRINDER',4,2),('SLOT_GRINDER',5,1),('SLOT_GRINDER',6,1),('SLOT_GRINDER',7,1),('SLOT_GRINDER',8,1),('SLOT_GRINDER',9,1),('SLOT_GRINDER',10,1),('SLOT_GRINDER',11,1),
 ('LIVE_HIGH_ROLLER',6,10),('LIVE_HIGH_ROLLER',7,9),('LIVE_HIGH_ROLLER',8,7),('LIVE_HIGH_ROLLER',9,6),('LIVE_HIGH_ROLLER',4,4),('LIVE_HIGH_ROLLER',2,3),('LIVE_HIGH_ROLLER',1,2),('LIVE_HIGH_ROLLER',12,2),('LIVE_HIGH_ROLLER',3,1),('LIVE_HIGH_ROLLER',5,1),('LIVE_HIGH_ROLLER',10,1),('LIVE_HIGH_ROLLER',11,1),
 ('SPORTS_BETTOR',10,12),('SPORTS_BETTOR',11,6),('SPORTS_BETTOR',1,2),('SPORTS_BETTOR',2,1),('SPORTS_BETTOR',4,1),('SPORTS_BETTOR',6,1),('SPORTS_BETTOR',3,1),('SPORTS_BETTOR',12,1),('SPORTS_BETTOR',5,1),('SPORTS_BETTOR',7,1),('SPORTS_BETTOR',8,1),('SPORTS_BETTOR',9,1),
 ('CASUAL',1,6),('CASUAL',2,3),('CASUAL',3,3),('CASUAL',12,3),('CASUAL',5,2),('CASUAL',4,2),('CASUAL',6,2),('CASUAL',9,2),('CASUAL',7,1),('CASUAL',8,1),('CASUAL',10,2),('CASUAL',11,1)
AS v(SEGMENT, CATEGORY_ID, WEIGHT);

-- Expand pool by weight, index each game per segment, capture pool sizes.
CREATE OR REPLACE TRANSIENT TABLE CORE._SEG_POOL AS
SELECT w.SEGMENT,
       g.GAME_TITLE_ID,
       ROW_NUMBER() OVER (PARTITION BY w.SEGMENT ORDER BY RANDOM()) - 1 AS PIDX
FROM CORE._SEG_CAT_WEIGHT w
JOIN CORE.GAME_TITLE_DIM g ON g.CATEGORY_ID = w.CATEGORY_ID
JOIN (SELECT SEQ4() AS n FROM TABLE(GENERATOR(ROWCOUNT => 12))) nums ON nums.n < w.WEIGHT;

CREATE OR REPLACE TRANSIENT TABLE CORE._SEG_POOL_SIZE AS
SELECT SEGMENT, COUNT(*) AS MSIZE FROM CORE._SEG_POOL GROUP BY SEGMENT;

-- ----------------------------------------------------------------------------
-- Historical game rounds (~1,000,000) with affinity skew + recency bias
-- ----------------------------------------------------------------------------
INSERT INTO CORE.GAME_ROUND_FACT
WITH base AS (
    SELECT SEQ8() AS rk, UNIFORM(1, 4000, RANDOM()) AS player_id
    FROM TABLE(GENERATOR(ROWCOUNT => 1000000))
),
withseg AS (
    SELECT b.rk, b.player_id, pl.PLAYER_SEGMENT AS seg
    FROM base b JOIN CORE.PLAYER_DIM pl ON pl.PLAYER_ID = b.player_id
),
pick AS (
    SELECT w.rk, w.player_id, w.seg,
           MOD(ABS(RANDOM()), sz.MSIZE) AS pidx
    FROM withseg w JOIN CORE._SEG_POOL_SIZE sz ON sz.SEGMENT = w.seg
),
chosen AS (
    SELECT p.rk, p.player_id, p.seg, pool.GAME_TITLE_ID
    FROM pick p JOIN CORE._SEG_POOL pool ON pool.SEGMENT = p.seg AND pool.PIDX = p.pidx
),
priced AS (
    SELECT c.*,
        -- recency-biased timestamp over last 180 days (squared -> more recent)
        DATEADD(second, -(POWER(UNIFORM(0, 1000, RANDOM())/1000.0, 2) * 180 * 86400)::INT, CURRENT_TIMESTAMP())::TIMESTAMP_NTZ AS ts,
        CASE c.seg
            WHEN 'LIVE_HIGH_ROLLER' THEN ROUND(UNIFORM(500, 20000, RANDOM())/100.0, 2)
            WHEN 'SPORTS_BETTOR'    THEN ROUND(UNIFORM(200, 10000, RANDOM())/100.0, 2)
            WHEN 'SLOT_GRINDER'     THEN ROUND(UNIFORM(20, 2000, RANDOM())/100.0, 2)
            ELSE ROUND(UNIFORM(10, 500, RANDOM())/100.0, 2)
        END AS stake
    FROM chosen c
)
SELECT
    'R' || pr.rk                                            AS ROUND_SOURCE_KEY,
    pr.player_id                                            AS PLAYER_ID,
    pr.GAME_TITLE_ID                                        AS GAME_TITLE_ID,
    ABS(HASH(pr.player_id, TO_DATE(pr.ts))) % 1000000000    AS SESSION_ID,
    978                                                     AS CURRENCY_ID,
    pr.stake                                                AS STAKE_CASH_AMT,
    pr.stake                                                AS STAKE_CASH_AMT_EUR,
    pr.stake                                                AS STAKE_TOTAL_AMT,
    pr.stake                                                AS STAKE_TOTAL_AMT_EUR,
    -- payout: mostly < stake (house edge), occasional big win
    ROUND(pr.stake * CASE WHEN UNIFORM(1,100,RANDOM()) <= 4 THEN UNIFORM(300,2500,RANDOM())/100.0
                          ELSE UNIFORM(0,180,RANDOM())/100.0 END, 2) AS PAYOUT_TOTAL_AMT_EUR,
    TRUE                                                    AS IS_ROUND_FINISHED_YN,
    (UNIFORM(1,100,RANDOM()) <= 8)                          AS IS_FREESPIN_YN,
    pr.ts                                                   AS ROUND_START_TIMESTAMP,
    DATEADD(second, UNIFORM(5, 120, RANDOM()), pr.ts)       AS ROUND_END_TIMESTAMP,
    TO_DATE(pr.ts)                                          AS ROUND_DATE,
    TRUE                                                    AS IS_ACTIVE
FROM priced pr;

DROP TABLE IF EXISTS CORE._SEG_POOL;
DROP TABLE IF EXISTS CORE._SEG_POOL_SIZE;
DROP TABLE IF EXISTS CORE._SEG_CAT_WEIGHT;

-- ----------------------------------------------------------------------------
-- Seed a few demo policy rows (Streamlit console manages the rest)
-- ----------------------------------------------------------------------------
-- Block 8 random games in Spain (market availability demo)
INSERT INTO APP.MARKET_GAME_BLOCK (REGION_CODE, GAME_TITLE_ID, REASON, UPDATED_BY)
SELECT 'ES', GAME_TITLE_ID, 'Regulatory restriction (DGOJ)', 'seed'
FROM CORE.GAME_TITLE_DIM SAMPLE (8 ROWS);

-- Exclude Esports category in Germany
INSERT INTO APP.MARKET_CATEGORY_EXCLUSION (REGION_CODE, CATEGORY_ID, REASON, UPDATED_BY)
VALUES ('DE', 11, 'Esports betting not permitted (GGL)', 'seed');

-- Demo player 1: blocked from jackpot category + live_baccarat subvertical
INSERT INTO APP.PLAYER_CATEGORY_EXCLUSION (PLAYER_ID, CATEGORY_ID, REASON, UPDATED_BY)
VALUES (1, 2, 'Player self-exclusion (jackpot)', 'seed');
INSERT INTO APP.PLAYER_SUBVERTICAL_EXCLUSION (PLAYER_ID, SUBVERTICAL, REASON, UPDATED_BY)
VALUES (1, 'live_baccarat', 'Player self-exclusion (baccarat)', 'seed');
