"""
PlayNova Policy Console
=======================
Operator console for managing business-rule policies that the PlayNova
recommendation engine enforces. Provides CRUD interfaces for market-level
game blocks, market/player category exclusions, player subvertical exclusions,
and a read-only eligible-catalog preview powered by a Snowflake dynamic table.
"""

import streamlit as st
import pandas as pd


def _rerun():
    """Version-safe rerun: Streamlit-in-Snowflake may ship an older build that
    exposes st.experimental_rerun() instead of st.rerun() (or neither)."""
    fn = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if fn:
        fn()


# ---------------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------------

def get_session():
    """Return a Snowpark Session, supporting both SiS and local execution."""
    try:
        from snowflake.snowpark.context import get_active_session
        return get_active_session()
    except (ImportError, ModuleNotFoundError, Exception):
        from snowflake.snowpark import Session
        return Session.builder.config("connection_name", "default").create()


# ---------------------------------------------------------------------------
# Cached reference-data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_regions(_session) -> pd.DataFrame:
    return _session.sql("SELECT REGION_ID, REGION_CODE, REGION_NAME FROM PLAYNOVA_RECS_DEMO.CORE.REGION_DIM ORDER BY REGION_CODE").to_pandas()


@st.cache_data(ttl=300)
def load_categories(_session) -> pd.DataFrame:
    return _session.sql("SELECT CATEGORY_ID, VERTICAL, SUBVERTICAL, CATEGORY_NAME FROM PLAYNOVA_RECS_DEMO.CORE.GAME_CATEGORY_DIM ORDER BY CATEGORY_NAME").to_pandas()


@st.cache_data(ttl=300)
def load_games(_session) -> pd.DataFrame:
    return _session.sql("SELECT GAME_TITLE_ID, CATEGORY_ID, GAME_TITLE, STUDIO_NAME FROM PLAYNOVA_RECS_DEMO.CORE.GAME_TITLE_DIM ORDER BY GAME_TITLE").to_pandas()


@st.cache_data(ttl=300)
def load_subverticals(_session) -> pd.DataFrame:
    return _session.sql("SELECT DISTINCT SUBVERTICAL FROM PLAYNOVA_RECS_DEMO.CORE.GAME_CATEGORY_DIM WHERE SUBVERTICAL IS NOT NULL ORDER BY SUBVERTICAL").to_pandas()


# ---------------------------------------------------------------------------
# Page config & branding
# ---------------------------------------------------------------------------

st.set_page_config(page_title="PlayNova Policy Console", layout="wide")

st.markdown("""
<style>
    [data-testid="stAppViewContainer"] {
        background-color: #1a0b2e;
    }
    [data-testid="stSidebar"] {
        background-color: #12071f;
    }
    [data-testid="stHeader"] {
        background-color: #1a0b2e;
    }
    h1, h2, h3, h4, p, span, label, div {
        color: #e8e0f0 !important;
    }
    .stMarkdown h1 {
        color: #7A3FF2 !important;
    }
    .stButton > button {
        background-color: #7A3FF2;
        color: white;
        border: none;
    }
    .stButton > button:hover {
        background-color: #6530d4;
        color: white;
    }
    /* Text / number input fields: dark bg so the light text is visible */
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input {
        background-color: #2a1a4a !important;
        color: #e8e0f0 !important;
    }
    input::placeholder { color: #9a86c0 !important; }
    /* Selectbox / multiselect closed control */
    [data-baseweb="select"] > div {
        background-color: #2a1a4a !important;
    }
    [data-baseweb="select"] div,
    [data-baseweb="select"] span,
    [data-baseweb="select"] input {
        color: #e8e0f0 !important;
    }
    /* Dropdown popover options (rendered in a portal) */
    [data-baseweb="popover"] [role="option"],
    ul[role="listbox"] li,
    [data-baseweb="menu"] li {
        background-color: #241043 !important;
        color: #e8e0f0 !important;
    }
    [data-baseweb="popover"] [role="option"]:hover,
    [data-baseweb="menu"] li:hover {
        background-color: #3a1f66 !important;
    }
</style>
""", unsafe_allow_html=True)

st.markdown("# PlayNova — Policy Console")

# ---------------------------------------------------------------------------
# Session & reference data
# ---------------------------------------------------------------------------

session = get_session()
regions_df = load_regions(session)
categories_df = load_categories(session)
games_df = load_games(session)
subverticals_df = load_subverticals(session)

# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

section = st.sidebar.radio("Section", [
    "Market availability",
    "Market category exclusions",
    "Player category exclusions",
    "Player subvertical exclusions",
    "Eligible catalog preview",
])


# ---------------------------------------------------------------------------
# (a) Market availability — APP.MARKET_GAME_BLOCK
# ---------------------------------------------------------------------------

def section_market_availability():
    st.header("Market Game Blocks")
    st.caption("Block specific games from appearing in a region's recommendations.")

    region_code = st.selectbox("Region", regions_df["REGION_CODE"].tolist(), key="mkt_avail_region")

    # Current blocks
    blocks_df = session.sql(
        """
        SELECT b.REGION_CODE, b.GAME_TITLE_ID, g.GAME_TITLE, b.REASON, b.UPDATED_AT, b.UPDATED_BY
        FROM PLAYNOVA_RECS_DEMO.APP.MARKET_GAME_BLOCK b
        JOIN PLAYNOVA_RECS_DEMO.CORE.GAME_TITLE_DIM g ON b.GAME_TITLE_ID = g.GAME_TITLE_ID
        WHERE b.REGION_CODE = ?
        ORDER BY g.GAME_TITLE
        """,
        params=[region_code],
    ).to_pandas()

    st.subheader(f"Currently blocked games in {region_code} ({len(blocks_df)})")
    if blocks_df.empty:
        st.info("No games are currently blocked in this region.")
    else:
        st.dataframe(blocks_df[["GAME_TITLE", "REASON", "UPDATED_AT", "UPDATED_BY"]], use_container_width=True)

        # Delete
        delete_titles = st.multiselect(
            "Select games to unblock",
            blocks_df["GAME_TITLE"].tolist(),
            key="mkt_avail_delete",
        )
        if st.button("Remove selected blocks", key="mkt_avail_del_btn"):
            ids_to_delete = blocks_df[blocks_df["GAME_TITLE"].isin(delete_titles)]["GAME_TITLE_ID"].tolist()
            for gid in ids_to_delete:
                session.sql(
                    "DELETE FROM PLAYNOVA_RECS_DEMO.APP.MARKET_GAME_BLOCK WHERE REGION_CODE = ? AND GAME_TITLE_ID = ?",
                    params=[region_code, int(gid)],
                ).collect()
            st.success(f"Removed {len(ids_to_delete)} block(s).")
            _rerun()

    # Add new blocks
    st.subheader("Add new blocks")
    blocked_ids = set(blocks_df["GAME_TITLE_ID"].tolist()) if not blocks_df.empty else set()
    available_games = games_df[~games_df["GAME_TITLE_ID"].isin(blocked_ids)]
    selected_games = st.multiselect(
        "Games to block",
        available_games["GAME_TITLE"].tolist(),
        key="mkt_avail_add",
    )
    reason = st.text_input("Reason", key="mkt_avail_reason", placeholder="e.g. regulatory restriction")

    if st.button("Add blocks", key="mkt_avail_add_btn"):
        if not selected_games:
            st.error("Select at least one game.")
        elif not reason.strip():
            st.error("Provide a reason.")
        else:
            ids_to_add = games_df[games_df["GAME_TITLE"].isin(selected_games)]["GAME_TITLE_ID"].tolist()
            for gid in ids_to_add:
                session.sql(
                    """
                    INSERT INTO PLAYNOVA_RECS_DEMO.APP.MARKET_GAME_BLOCK
                        (REGION_CODE, GAME_TITLE_ID, REASON, UPDATED_AT, UPDATED_BY)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP(), 'policy_console')
                    """,
                    params=[region_code, int(gid), reason.strip()],
                ).collect()
            st.success(f"Blocked {len(ids_to_add)} game(s) in {region_code}.")
            _rerun()


# ---------------------------------------------------------------------------
# (b) Market category exclusions — APP.MARKET_CATEGORY_EXCLUSION
# ---------------------------------------------------------------------------

def section_market_category_exclusions():
    st.header("Market Category Exclusions")
    st.caption("Exclude entire game categories from a region.")

    region_code = st.selectbox("Region", regions_df["REGION_CODE"].tolist(), key="mkt_cat_region")

    exclusions_df = session.sql(
        """
        SELECT e.REGION_CODE, e.CATEGORY_ID, c.CATEGORY_NAME, e.REASON, e.UPDATED_AT, e.UPDATED_BY
        FROM PLAYNOVA_RECS_DEMO.APP.MARKET_CATEGORY_EXCLUSION e
        JOIN PLAYNOVA_RECS_DEMO.CORE.GAME_CATEGORY_DIM c ON e.CATEGORY_ID = c.CATEGORY_ID
        WHERE e.REGION_CODE = ?
        ORDER BY c.CATEGORY_NAME
        """,
        params=[region_code],
    ).to_pandas()

    st.subheader(f"Current exclusions in {region_code} ({len(exclusions_df)})")
    if exclusions_df.empty:
        st.info("No category exclusions in this region.")
    else:
        st.dataframe(exclusions_df[["CATEGORY_NAME", "REASON", "UPDATED_AT", "UPDATED_BY"]], use_container_width=True)

        delete_cats = st.multiselect(
            "Select categories to remove exclusion",
            exclusions_df["CATEGORY_NAME"].tolist(),
            key="mkt_cat_delete",
        )
        if st.button("Remove selected exclusions", key="mkt_cat_del_btn"):
            ids_to_delete = exclusions_df[exclusions_df["CATEGORY_NAME"].isin(delete_cats)]["CATEGORY_ID"].tolist()
            for cid in ids_to_delete:
                session.sql(
                    "DELETE FROM PLAYNOVA_RECS_DEMO.APP.MARKET_CATEGORY_EXCLUSION WHERE REGION_CODE = ? AND CATEGORY_ID = ?",
                    params=[region_code, int(cid)],
                ).collect()
            st.success(f"Removed {len(ids_to_delete)} exclusion(s).")
            _rerun()

    # Add
    st.subheader("Add category exclusions")
    excluded_ids = set(exclusions_df["CATEGORY_ID"].tolist()) if not exclusions_df.empty else set()
    available_cats = categories_df[~categories_df["CATEGORY_ID"].isin(excluded_ids)]
    selected_cats = st.multiselect(
        "Categories to exclude",
        available_cats["CATEGORY_NAME"].tolist(),
        key="mkt_cat_add",
    )
    reason = st.text_input("Reason", key="mkt_cat_reason", placeholder="e.g. not licensed in region")

    if st.button("Add exclusions", key="mkt_cat_add_btn"):
        if not selected_cats:
            st.error("Select at least one category.")
        elif not reason.strip():
            st.error("Provide a reason.")
        else:
            ids_to_add = categories_df[categories_df["CATEGORY_NAME"].isin(selected_cats)]["CATEGORY_ID"].tolist()
            for cid in ids_to_add:
                session.sql(
                    """
                    INSERT INTO PLAYNOVA_RECS_DEMO.APP.MARKET_CATEGORY_EXCLUSION
                        (REGION_CODE, CATEGORY_ID, REASON, UPDATED_AT, UPDATED_BY)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP(), 'policy_console')
                    """,
                    params=[region_code, int(cid), reason.strip()],
                ).collect()
            st.success(f"Excluded {len(ids_to_add)} category(ies) in {region_code}.")
            _rerun()


# ---------------------------------------------------------------------------
# (c) Player category exclusions — APP.PLAYER_CATEGORY_EXCLUSION
# ---------------------------------------------------------------------------

def section_player_category_exclusions():
    st.header("Player Category Exclusions")
    st.caption("Exclude categories for a specific player (e.g. self-exclusion requests).")

    player_id = st.number_input("Player ID", min_value=1, step=1, key="pl_cat_pid")

    exclusions_df = session.sql(
        """
        SELECT e.PLAYER_ID, e.CATEGORY_ID, c.CATEGORY_NAME, e.REASON, e.UPDATED_AT, e.UPDATED_BY
        FROM PLAYNOVA_RECS_DEMO.APP.PLAYER_CATEGORY_EXCLUSION e
        JOIN PLAYNOVA_RECS_DEMO.CORE.GAME_CATEGORY_DIM c ON e.CATEGORY_ID = c.CATEGORY_ID
        WHERE e.PLAYER_ID = ?
        ORDER BY c.CATEGORY_NAME
        """,
        params=[int(player_id)],
    ).to_pandas()

    st.subheader(f"Current exclusions for player {int(player_id)} ({len(exclusions_df)})")
    if exclusions_df.empty:
        st.info("No category exclusions for this player.")
    else:
        st.dataframe(exclusions_df[["CATEGORY_NAME", "REASON", "UPDATED_AT", "UPDATED_BY"]], use_container_width=True)

        delete_cats = st.multiselect(
            "Select categories to remove exclusion",
            exclusions_df["CATEGORY_NAME"].tolist(),
            key="pl_cat_delete",
        )
        if st.button("Remove selected exclusions", key="pl_cat_del_btn"):
            ids_to_delete = exclusions_df[exclusions_df["CATEGORY_NAME"].isin(delete_cats)]["CATEGORY_ID"].tolist()
            for cid in ids_to_delete:
                session.sql(
                    "DELETE FROM PLAYNOVA_RECS_DEMO.APP.PLAYER_CATEGORY_EXCLUSION WHERE PLAYER_ID = ? AND CATEGORY_ID = ?",
                    params=[int(player_id), int(cid)],
                ).collect()
            st.success(f"Removed {len(ids_to_delete)} exclusion(s).")
            _rerun()

    # Add
    st.subheader("Add category exclusions")
    excluded_ids = set(exclusions_df["CATEGORY_ID"].tolist()) if not exclusions_df.empty else set()
    available_cats = categories_df[~categories_df["CATEGORY_ID"].isin(excluded_ids)]
    selected_cats = st.multiselect(
        "Categories to exclude",
        available_cats["CATEGORY_NAME"].tolist(),
        key="pl_cat_add",
    )
    reason = st.text_input("Reason", key="pl_cat_reason", placeholder="e.g. player self-exclusion request")

    if st.button("Add exclusions", key="pl_cat_add_btn"):
        if not selected_cats:
            st.error("Select at least one category.")
        elif not reason.strip():
            st.error("Provide a reason.")
        else:
            ids_to_add = categories_df[categories_df["CATEGORY_NAME"].isin(selected_cats)]["CATEGORY_ID"].tolist()
            for cid in ids_to_add:
                session.sql(
                    """
                    INSERT INTO PLAYNOVA_RECS_DEMO.APP.PLAYER_CATEGORY_EXCLUSION
                        (PLAYER_ID, CATEGORY_ID, REASON, UPDATED_AT, UPDATED_BY)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP(), 'policy_console')
                    """,
                    params=[int(player_id), int(cid), reason.strip()],
                ).collect()
            st.success(f"Excluded {len(ids_to_add)} category(ies) for player {int(player_id)}.")
            _rerun()


# ---------------------------------------------------------------------------
# (d) Player subvertical exclusions — APP.PLAYER_SUBVERTICAL_EXCLUSION
# ---------------------------------------------------------------------------

def section_player_subvertical_exclusions():
    st.header("Player Subvertical Exclusions")
    st.caption("Exclude game subverticals for a specific player.")

    player_id = st.number_input("Player ID", min_value=1, step=1, key="pl_sv_pid")

    exclusions_df = session.sql(
        """
        SELECT PLAYER_ID, SUBVERTICAL, REASON, UPDATED_AT, UPDATED_BY
        FROM PLAYNOVA_RECS_DEMO.APP.PLAYER_SUBVERTICAL_EXCLUSION
        WHERE PLAYER_ID = ?
        ORDER BY SUBVERTICAL
        """,
        params=[int(player_id)],
    ).to_pandas()

    st.subheader(f"Current subvertical exclusions for player {int(player_id)} ({len(exclusions_df)})")
    if exclusions_df.empty:
        st.info("No subvertical exclusions for this player.")
    else:
        st.dataframe(exclusions_df[["SUBVERTICAL", "REASON", "UPDATED_AT", "UPDATED_BY"]], use_container_width=True)

        delete_svs = st.multiselect(
            "Select subverticals to remove exclusion",
            exclusions_df["SUBVERTICAL"].tolist(),
            key="pl_sv_delete",
        )
        if st.button("Remove selected exclusions", key="pl_sv_del_btn"):
            for sv in delete_svs:
                session.sql(
                    "DELETE FROM PLAYNOVA_RECS_DEMO.APP.PLAYER_SUBVERTICAL_EXCLUSION WHERE PLAYER_ID = ? AND SUBVERTICAL = ?",
                    params=[int(player_id), sv],
                ).collect()
            st.success(f"Removed {len(delete_svs)} exclusion(s).")
            _rerun()

    # Add
    st.subheader("Add subvertical exclusions")
    excluded_svs = set(exclusions_df["SUBVERTICAL"].tolist()) if not exclusions_df.empty else set()
    available_svs = subverticals_df[~subverticals_df["SUBVERTICAL"].isin(excluded_svs)]["SUBVERTICAL"].tolist()
    selected_svs = st.multiselect(
        "Subverticals to exclude",
        available_svs,
        key="pl_sv_add",
    )
    reason = st.text_input("Reason", key="pl_sv_reason", placeholder="e.g. responsible gaming measure")

    if st.button("Add exclusions", key="pl_sv_add_btn"):
        if not selected_svs:
            st.error("Select at least one subvertical.")
        elif not reason.strip():
            st.error("Provide a reason.")
        else:
            for sv in selected_svs:
                session.sql(
                    """
                    INSERT INTO PLAYNOVA_RECS_DEMO.APP.PLAYER_SUBVERTICAL_EXCLUSION
                        (PLAYER_ID, SUBVERTICAL, REASON, UPDATED_AT, UPDATED_BY)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP(), 'policy_console')
                    """,
                    params=[int(player_id), sv, reason.strip()],
                ).collect()
            st.success(f"Excluded {len(selected_svs)} subvertical(s) for player {int(player_id)}.")
            _rerun()


# ---------------------------------------------------------------------------
# (e) Eligible catalog preview — FEATURES.MARKET_ELIGIBLE_GAMES
# ---------------------------------------------------------------------------

def section_eligible_catalog_preview():
    st.header("Eligible Catalog Preview")
    st.caption("Read-only view of the dynamic table FEATURES.MARKET_ELIGIBLE_GAMES (1-hour target lag).")

    region_code = st.selectbox("Region", regions_df["REGION_CODE"].tolist(), key="elig_region")

    summary_df = session.sql(
        """
        SELECT
            IS_ELIGIBLE,
            COUNT(*) AS GAME_COUNT
        FROM PLAYNOVA_RECS_DEMO.FEATURES.MARKET_ELIGIBLE_GAMES
        WHERE REGION_CODE = ?
        GROUP BY IS_ELIGIBLE
        """,
        params=[region_code],
    ).to_pandas()

    eligible_count = int(summary_df.loc[summary_df["IS_ELIGIBLE"] == True, "GAME_COUNT"].sum()) if not summary_df.empty else 0
    ineligible_count = int(summary_df.loc[summary_df["IS_ELIGIBLE"] == False, "GAME_COUNT"].sum()) if not summary_df.empty else 0

    col1, col2 = st.columns(2)
    col1.metric("Eligible games", eligible_count)
    col2.metric("Ineligible games", ineligible_count)

    # Ineligible reasons chart
    reasons_df = session.sql(
        """
        SELECT INELIGIBLE_REASON, COUNT(*) AS COUNT
        FROM PLAYNOVA_RECS_DEMO.FEATURES.MARKET_ELIGIBLE_GAMES
        WHERE REGION_CODE = ? AND IS_ELIGIBLE = FALSE AND INELIGIBLE_REASON IS NOT NULL
        GROUP BY INELIGIBLE_REASON
        ORDER BY COUNT DESC
        """,
        params=[region_code],
    ).to_pandas()

    if not reasons_df.empty:
        st.subheader("Ineligible games by reason")
        chart_data = reasons_df.set_index("INELIGIBLE_REASON")
        st.bar_chart(chart_data["COUNT"])
    else:
        st.info("No ineligible games with reasons to display.")

    # Eligible games table
    st.subheader("Eligible games")
    eligible_df = session.sql(
        """
        SELECT g.GAME_TITLE, c.CATEGORY_NAME
        FROM PLAYNOVA_RECS_DEMO.FEATURES.MARKET_ELIGIBLE_GAMES e
        JOIN PLAYNOVA_RECS_DEMO.CORE.GAME_TITLE_DIM g ON e.GAME_TITLE_ID = g.GAME_TITLE_ID
        JOIN PLAYNOVA_RECS_DEMO.CORE.GAME_CATEGORY_DIM c ON e.CATEGORY_ID = c.CATEGORY_ID
        WHERE e.REGION_CODE = ? AND e.IS_ELIGIBLE = TRUE
        ORDER BY g.GAME_TITLE
        """,
        params=[region_code],
    ).to_pandas()

    st.dataframe(eligible_df, use_container_width=True, height=400)
    st.caption("This dynamic table refreshes with a 1-hour target lag from upstream policy tables.")


# ---------------------------------------------------------------------------
# Route to selected section
# ---------------------------------------------------------------------------

if section == "Market availability":
    section_market_availability()
elif section == "Market category exclusions":
    section_market_category_exclusions()
elif section == "Player category exclusions":
    section_player_category_exclusions()
elif section == "Player subvertical exclusions":
    section_player_subvertical_exclusions()
elif section == "Eligible catalog preview":
    section_eligible_catalog_preview()
