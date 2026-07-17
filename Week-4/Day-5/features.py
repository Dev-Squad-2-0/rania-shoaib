"""Shared feature engineering used by both the notebook and inference.py.

Must live in an importable .py file (not a notebook cell) so that
joblib can pickle/unpickle the FunctionTransformer that wraps
engineer_features — pickle needs to resolve it to a real module path.
"""
import numpy as np
import pandas as pd

AGE_BINS = [0, 25, 35, 45, 55, 65, 100]
AGE_LABELS = ["<=25", "26-35", "36-45", "46-55", "56-65", "65+"]
HOURS_BINS = [0, 20, 35, 40, 50, 100]
HOURS_LABELS = ["part_time_le20", "reduced_21_35", "standard_36_40", "over_41_50", "heavy_50plus"]


def engineer_features(frame):
    out = frame.copy()
    out["age_bucket"] = pd.cut(out["age"], bins=AGE_BINS, labels=AGE_LABELS, right=True).astype(str)
    out["hours_bucket"] = pd.cut(out["hours-per-week"], bins=HOURS_BINS, labels=HOURS_LABELS, right=True).astype(str)
    out["has_capital_gain"] = (out["capital-gain"] > 0).astype(int)
    out["has_capital_loss"] = (out["capital-loss"] > 0).astype(int)
    out["higher_education"] = (out["education-num"] >= 13).astype(int)
    out["log_capital_gain"] = np.log1p(out["capital-gain"])
    out["edu_hours_interaction"] = out["education-num"] * out["hours-per-week"]
    out["net_capital"] = out["capital-gain"] - out["capital-loss"]
    return out