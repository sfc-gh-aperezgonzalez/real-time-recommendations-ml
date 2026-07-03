"""Train + register PlayNova recommendation models.

Trains two XGBoost models from the offline feature Dynamic Tables (the same
sources that feed the Online Feature Store, preserving training-serving parity):

  1. RANKER (PLAYNOVA_RANKER) - per (player, game) engagement scorer used by the
     orchestrator. Feature vector matches services/orchestrator/app.py exactly:
        [affinity_for_category, game_rounds_30d_norm, popularity_trend, rtp/100, player_rounds_30d]
  2. PROPENSITY (PLAYNOVA_PROPENSITY) - player-level next-7d play propensity
     (affinity vector + behavior). Demonstrates the batch-scored model.

Both are registered in the Snowflake Model Registry (ML schema). The ranker is
served for inference by the registry's real-time inference service on SPCS
(see ml/deploy_inference.py); no model artifact is baked into any image.

Usage: python ml/train.py
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

from _session import DEMO_DB, get_session

AFF_COL = {
    1: "AFF_SLOTS", 2: "AFF_JACKPOT", 3: "AFF_CLASSIC", 4: "AFF_TABLE", 5: "AFF_SCRATCH",
    6: "AFF_LIVE_ROULETTE", 7: "AFF_LIVE_BLACKJACK", 8: "AFF_LIVE_BACCARAT", 9: "AFF_GAME_SHOW",
    10: "AFF_SPORTSBOOK", 11: "AFF_ESPORTS", 12: "AFF_MEGAWAYS",
}
N_POS = 60000

# Register the retrained ranker as a NEW version so V1 stays available for rollback.
RANKER_VERSION = "V2"

# Real-time recency feature normalization cap. RECENT_CAT_ACTIVITY_NORM =
# LEAST(cat_plays_24h, RECENCY_CAP) / RECENCY_CAP. MUST match the identical
# constant in services/orchestrator/app.py so training and serving normalize the
# streaming feature the same way (training-serving parity). CAP chosen from the
# observed 24h per-category play distribution (p90=9), so heavy players saturate
# near 1.0 without a single outlier dominating.
RECENCY_CAP = 10


def load_frames(session):
    aff = session.sql(f"SELECT * FROM {DEMO_DB}.FEATURES.PLAYER_AFFINITY_PROFILE").to_pandas().set_index("PLAYER_ID")
    beh = session.sql(
        f"SELECT PLAYER_ID, ROUNDS_30D, ROUNDS_7D, AVG_STAKE_EUR, DAYS_SINCE_LAST_PLAY "
        f"FROM {DEMO_DB}.FEATURES.PLAYER_BEHAVIOR_PROFILE"
    ).to_pandas().set_index("PLAYER_ID")
    games = session.sql(
        f"SELECT GAME_TITLE_ID, CATEGORY_ID, ZEROIFNULL(ROUNDS_30D) ROUNDS_30D, "
        f"ZEROIFNULL(POPULARITY_TREND) POPULARITY_TREND, ZEROIFNULL(RETURN_TO_PLAYER_PCT) RTP "
        f"FROM {DEMO_DB}.FEATURES.GAME_CATALOG_PROFILE"
    ).to_pandas().set_index("GAME_TITLE_ID")
    pos = session.sql(
        f"SELECT PLAYER_ID, GAME_TITLE_ID, CATEGORY_ID, LAST_PLAY_TS "
        f"FROM {DEMO_DB}.FEATURES.PLAYER_GAME_INTERACTION "
        f"SAMPLE ({N_POS} ROWS)"
    ).to_pandas()
    played = session.sql(
        f"SELECT PLAYER_ID, GAME_TITLE_ID FROM {DEMO_DB}.FEATURES.PLAYER_GAME_INTERACTION"
    ).to_pandas()
    return aff, beh, games, pos, played


def _compute_recency(session, spine: list[tuple]) -> dict[int, float]:
    """Point-in-time RECENT_CAT_ACTIVITY_NORM for each training row.

    Computed IN SNOWFLAKE from the authoritative event history
    (CORE.GAME_ROUND_FACT) using the SAME 24h-count definition that the online
    USER_CATEGORY_RECENT feature view serves (Feature.count over a 24h window,
    per player, per category). We compute it here rather than via
    generate_training_set because the streaming FV's offline tiles only cover the
    last ~20 days while training positives span ~6 months; GAME_ROUND_FACT is the
    same event source the FV was backfilled from, so the feature definition (and
    therefore training-serving parity) is preserved.

    Leakage-safe: the window is half-open [REF_TS - 24h, REF_TS), so the label
    event at REF_TS itself is never counted. spine = [(idx, player_id,
    category_id, ref_ts_str), ...].
    """
    spine_df = pd.DataFrame(spine, columns=["IDX", "PLAYER_ID", "CATEGORY_ID", "REF_TS"])
    session.write_pandas(
        spine_df, "TMP_RANKER_SPINE", database=DEMO_DB, schema="FEATURES",
        auto_create_table=True, overwrite=True, table_type="temporary",
    )
    rows = session.sql(f"""
        SELECT s.IDX AS IDX,
               LEAST(COUNT_IF(g.CATEGORY_ID = s.CATEGORY_ID), {RECENCY_CAP}) / {float(RECENCY_CAP)} AS REC
        FROM {DEMO_DB}.FEATURES.TMP_RANKER_SPINE s
        LEFT JOIN {DEMO_DB}.CORE.GAME_ROUND_FACT f
               ON f.PLAYER_ID = s.PLAYER_ID
              AND f.ROUND_START_TIMESTAMP >= DATEADD('hour', -24, TO_TIMESTAMP_NTZ(s.REF_TS))
              AND f.ROUND_START_TIMESTAMP <  TO_TIMESTAMP_NTZ(s.REF_TS)
        LEFT JOIN {DEMO_DB}.CORE.GAME_TITLE_DIM g
               ON g.GAME_TITLE_ID = f.GAME_TITLE_ID
        GROUP BY s.IDX
    """).collect()
    return {int(r["IDX"]): float(r["REC"]) for r in rows}


def build_ranker_dataset(session, aff, beh, games, pos, played):
    max_pop = max(games["ROUNDS_30D"].max(), 1)
    played_set = played.groupby("PLAYER_ID")["GAME_TITLE_ID"].apply(set).to_dict()
    game_ids = games.index.to_numpy()
    rng = np.random.default_rng(42)

    def feat(player_id, game_id, cat_id):
        a = aff.loc[player_id] if player_id in aff.index else None
        aff_val = float(a[AFF_COL.get(cat_id, "AFF_SLOTS")]) if a is not None else 0.0
        g = games.loc[game_id]
        p_rounds = float(beh.loc[player_id]["ROUNDS_30D"]) if player_id in beh.index else 0.0
        return [aff_val, float(g["ROUNDS_30D"]) / max_pop, float(g["POPULARITY_TREND"]),
                float(g["RTP"]) / 100.0, p_rounds]

    rows, labels, spine = [], [], []
    for r in pos.itertuples(index=False):
        pid, gid, cid = int(r.PLAYER_ID), int(r.GAME_TITLE_ID), int(r.CATEGORY_ID)
        if gid not in games.index:
            continue
        # Reference time for the point-in-time recency feature = the play event
        # this positive represents. The paired negative shares the same reference
        # time (a valid contrastive pair "at this moment, what was the player into?").
        ref_ts = str(r.LAST_PLAY_TS)
        rows.append(feat(pid, gid, cid)); labels.append(1)
        spine.append((len(rows) - 1, pid, cid, ref_ts))
        # one negative: a random game the player has not played
        seen = played_set.get(pid, set())
        for _ in range(5):
            ng = int(game_ids[rng.integers(len(game_ids))])
            if ng not in seen:
                ng_cat = int(games.loc[ng]["CATEGORY_ID"])
                rows.append(feat(pid, ng, ng_cat)); labels.append(0)
                spine.append((len(rows) - 1, pid, ng_cat, ref_ts))
                break

    # 6th feature: real-time recency, assembled in Snowflake, leakage-safe.
    recency = _compute_recency(session, spine)
    rec_col = np.array([recency.get(i, 0.0) for i in range(len(rows))], dtype=float)
    X = np.column_stack([np.array(rows, dtype=float), rec_col])
    y = np.array(labels, dtype=int)
    print(f"[train] recency feature: nonzero={(rec_col > 0).sum()}/{len(rec_col)} "
          f"mean={rec_col.mean():.3f} max={rec_col.max():.3f}")
    return X, y


def build_propensity_dataset(aff, beh):
    df = aff.join(beh, how="inner")
    feat_cols = list(AFF_COL.values()) + ["ROUNDS_30D", "AVG_STAKE_EUR", "DAYS_SINCE_LAST_PLAY"]
    df = df.fillna(0.0)
    X = df[feat_cols].to_numpy(dtype=float)
    # High-engagement propensity: player in the top ~35% by recent (7d) activity.
    # Data-driven threshold guarantees two well-populated classes.
    thr = df["ROUNDS_7D"].quantile(0.65)
    y = (df["ROUNDS_7D"] > thr).astype(int).to_numpy()
    return X, y, feat_cols


def train_xgb(X, y, name, monotone_constraints=None):
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    kwargs = dict(
        n_estimators=120, max_depth=6, learning_rate=0.1, subsample=0.9,
        colsample_bytree=0.9, eval_metric="auc", n_jobs=4,
    )
    # Monotone constraint: force a feature to only ever INCREASE the predicted
    # engagement (used for RECENT_CAT_ACTIVITY_NORM). Encodes the domain prior that
    # a player's recent activity in a category must never lower that category's
    # recommendation; the model still learns the magnitude from data. Without this,
    # XGBoost learns a confounded non-monotonic (sometimes negative) recency effect
    # because category-level recency co-occurs with game-level negatives.
    if monotone_constraints is not None:
        kwargs["monotone_constraints"] = monotone_constraints
    clf = xgb.XGBClassifier(**kwargs)
    clf.fit(Xtr, ytr)
    auc = roc_auc_score(yte, clf.predict_proba(Xte)[:, 1])
    print(f"[train] {name}: rows={len(y)} positives={int(y.sum())} test_AUC={auc:.4f}")
    return clf, Xte[:5]


def _log_feature_importance(clf, feat_names) -> None:
    """Print gain-based feature importance so we can confirm the new recency
    feature earns real (but not dominant) weight, and eyeball for leakage."""
    try:
        imp = clf.feature_importances_
        pairs = sorted(zip(feat_names, imp), key=lambda p: p[1], reverse=True)
        print("[train] ranker feature importance:")
        for name, val in pairs:
            print(f"          {name:<24} {val:.4f}")
    except Exception as exc:  # noqa: BLE001
        print(f"[train] feature importance unavailable: {exc}")


def register(session, clf, name, sample_X, feat_names, version="V1"):
    from snowflake.ml.registry import Registry
    reg = Registry(session, database_name=DEMO_DB, schema_name="ML")
    sample = pd.DataFrame(sample_X, columns=feat_names)
    try:
        reg.log_model(
            model=clf, model_name=name, version_name=version,
            sample_input_data=sample, comment="PlayNova recommendation model",
            options={"relax_version": True},
        )
        print(f"[train] registered {name}/{version} in Model Registry")
    except Exception as exc:  # noqa: BLE001
        print(f"[train] registry log for {name}: {exc}")


def main() -> None:
    session = get_session()
    try:
        aff, beh, games, pos, played = load_frames(session)
        print(f"[train] loaded: players={len(aff)} games={len(games)} positives_sampled={len(pos)}")

        # Ranker. RECENT_CAT_ACTIVITY_NORM (last feature) is monotone-increasing:
        # recent category activity can only lift a recommendation, never lower it.
        Xr, yr = build_ranker_dataset(session, aff, beh, games, pos, played)
        ranker, ranker_sample = train_xgb(Xr, yr, "PLAYNOVA_RANKER",
                                          monotone_constraints=(0, 0, 0, 0, 0, 1))
        ranker_feats = ["AFF_FOR_CATEGORY", "GAME_ROUNDS_30D_NORM", "POPULARITY_TREND",
                        "RTP_FRAC", "PLAYER_ROUNDS_30D", "RECENT_CAT_ACTIVITY_NORM"]
        _log_feature_importance(ranker, ranker_feats)
        register(session, ranker, "PLAYNOVA_RANKER", ranker_sample, ranker_feats,
                 version=RANKER_VERSION)

        # Batch propensity
        Xp, yp, pcols = build_propensity_dataset(aff, beh)
        prop, prop_sample = train_xgb(Xp, yp, "PLAYNOVA_PROPENSITY")
        register(session, prop, "PLAYNOVA_PROPENSITY", prop_sample, pcols)
        print("[train] DONE")
    finally:
        session.close()


if __name__ == "__main__":
    main()
