"""Log the real held-out test AUC onto the already-deployed PlayNova V1 models.

Scene 5 of the demo opens the Model Registry and says "here are the metrics it was
logged with, like its AUC." The models were registered without metrics
(metadata = {}), so nothing showed. This script does NOT retrain or re-serve: it
reconstructs a held-out split with the same methodology as ml/train.py, runs the
*deployed* V1 model's predict_proba over it, computes ROC AUC against the real
labels, and attaches it to the version via set_metric. Honest, and it makes the
registry UI show a real number.

Usage: python ml/set_model_metrics.py
"""
from __future__ import annotations

import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

from _session import DEMO_DB, get_session
from train import (
    build_propensity_dataset,
    build_ranker_dataset,
    load_frames,
)

RANKER_FEATS = ["AFF_FOR_CATEGORY", "GAME_ROUNDS_30D_NORM", "POPULARITY_TREND",
                "RTP_FRAC", "PLAYER_ROUNDS_30D", "RECENT_CAT_ACTIVITY_NORM"]
RANKER_VERSION = "V2"


def _proba1(mv, X: pd.DataFrame) -> pd.Series:
    """Run the deployed model's predict_proba and return P(class=1).

    Fill NaNs with 0.0 first, exactly as the live orchestrator does when it builds
    the feature payload (float(c.get(k) or 0.0)), so we evaluate the model as served.
    """
    out = mv.run(X.fillna(0.0), function_name="predict_proba")
    if hasattr(out, "to_pandas"):
        out = out.to_pandas()
    if "output_feature_1" not in out.columns:
        raise RuntimeError(f"predict_proba output missing output_feature_1; got {list(out.columns)}")
    return out["output_feature_1"].astype(float).reset_index(drop=True)


def evaluate_and_log(session, model_name: str, X, y, feat_names: list[str], version: str = "V1") -> float:
    from snowflake.ml.registry import Registry
    reg = Registry(session, database_name=DEMO_DB, schema_name="ML")
    mv = reg.get_model(model_name).version(version)

    _, X_te, _, y_te = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    X_df = pd.DataFrame(X_te, columns=feat_names)
    proba1 = _proba1(mv, X_df)

    # Align + drop any rows the model could not score, then compute a clean AUC.
    ev = pd.DataFrame({"y": pd.Series(y_te).reset_index(drop=True), "p": proba1}).dropna()
    if ev["y"].nunique() < 2 or len(ev) == 0:
        raise RuntimeError(f"{model_name}: cannot compute AUC (rows={len(ev)}, classes={ev['y'].nunique()})")
    auc = float(roc_auc_score(ev["y"], ev["p"]))

    mv.set_metric("test_auc", round(auc, 4))
    mv.set_metric("test_rows", int(len(ev)))
    mv.set_metric("test_positive_rate", round(float(ev["y"].mean()), 4))
    print(f"[metrics] {model_name}/{version}: test_auc={auc:.4f} on {len(ev)} held-out rows -> set_metric done")
    return auc


def main() -> None:
    session = get_session()
    try:
        aff, beh, games, pos, played = load_frames(session)

        Xr, yr = build_ranker_dataset(session, aff, beh, games, pos, played)
        evaluate_and_log(session, "PLAYNOVA_RANKER", Xr, yr, RANKER_FEATS, version=RANKER_VERSION)

        # Propensity's label is "played in the next 7 days"; on stale demo data the
        # 7-day window can be empty (one class) and AUC is undefined. Never fabricate
        # a number - skip with a clear warning rather than log a bogus metric.
        Xp, yp, pcols = build_propensity_dataset(aff, beh)
        try:
            evaluate_and_log(session, "PLAYNOVA_PROPENSITY", Xp, yp, pcols)
        except RuntimeError as exc:
            print(f"[metrics] SKIP PLAYNOVA_PROPENSITY: {exc}")
        print("[metrics] DONE")
    finally:
        session.close()


if __name__ == "__main__":
    main()
