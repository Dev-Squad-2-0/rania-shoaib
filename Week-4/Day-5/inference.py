"""
End-to-end inference for the income-prediction model.
Loads saved artifacts, validates input, returns probability,
predicted class, and top-3 contributing features per row.
"""
import pandas as pd
import numpy as np
import joblib
import shap

REQUIRED_COLUMNS = [
    "age", "workclass", "fnlwgt", "education", "education-num",
    "marital-status", "occupation", "relationship", "race", "sex",
    "capital-gain", "capital-loss", "hours-per-week", "native-country",
]
# fnlwgt/education aren't used by feature_cols downstream but are accepted
# for schema compatibility with the raw dataset; drop silently if absent.

FEATURE_COLS = [
    "age", "workclass", "education-num", "marital-status", "occupation",
    "relationship", "race", "sex", "capital-gain", "capital-loss",
    "hours-per-week", "native-country",
]


class InferenceInputError(ValueError):
    """Raised when input rows fail schema validation."""


class IncomeModel:
    def __init__(self, model_dir="models"):
        self.pipeline = joblib.load(f"{model_dir}/day4_pipeline.joblib")
        self.raw_pipe = joblib.load(f"{model_dir}/hgb_pipe.joblib")
        self.threshold = joblib.load(f"{model_dir}/day4_threshold.joblib")

        clf = self.raw_pipe.named_steps["clf"]
        self.explainer = shap.TreeExplainer(clf)
        self.fe_step = self.raw_pipe.named_steps["feature_engineering"]
        self.prep_step = self.raw_pipe.named_steps["preprocessing"]
        self.feature_names_out = self.prep_step.get_feature_names_out()

    def _validate(self, df: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in FEATURE_COLS if c not in df.columns]
        if missing:
            raise InferenceInputError(f"Missing required columns: {missing}")

        df = df.copy()
        # Numeric columns must be numeric-coercible
        for col in ["age", "education-num", "capital-gain", "capital-loss", "hours-per-week"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            if df[col].isna().any():
                raise InferenceInputError(f"Column '{col}' has non-numeric or missing values")

        # Unseen categories: not an error — OneHotEncoder(handle_unknown="ignore")
        # already handles these by encoding them as all-zeros. We just log them.
        return df[FEATURE_COLS]

    def predict(self, input_data):
        """
        input_data: dict (single row), list[dict], or path to CSV.
        Returns a DataFrame with probability, predicted_class, top_3_features.
        """
        if isinstance(input_data, str):
            df = pd.read_csv(input_data)
        elif isinstance(input_data, dict):
            df = pd.DataFrame([input_data])
        elif isinstance(input_data, list):
            df = pd.DataFrame(input_data)
        elif isinstance(input_data, pd.DataFrame):
            df = input_data.copy()
        else:
            raise InferenceInputError(f"Unsupported input type: {type(input_data)}")

        df = self._validate(df)

        probs = self.pipeline.predict_proba(df)[:, 1]
        preds = (probs >= self.threshold).astype(int)

        # SHAP on the raw (uncalibrated) model, same rationale as Task 3
        df_fe = self.fe_step.transform(df)
        df_transformed = self.prep_step.transform(df_fe)
        shap_values = self.explainer(df_transformed)

        top3_list = []
        for row_sv in shap_values.values:
            top3_idx = np.argsort(np.abs(row_sv))[::-1][:3]
            top3 = [(self.feature_names_out[i], round(float(row_sv[i]), 4)) for i in top3_idx]
            top3_list.append(top3)

        return pd.DataFrame({
            "probability": probs,
            "predicted_class": preds,
            "top_3_features": top3_list,
        }, index=df.index)


if __name__ == "__main__":
    model = IncomeModel()
    sample = {
        "age": 45, "workclass": "Private", "education-num": 13,
        "marital-status": "Married-civ-spouse", "occupation": "Exec-managerial",
        "relationship": "Husband", "race": "White", "sex": "Male",
        "capital-gain": 5000, "capital-loss": 0, "hours-per-week": 50,
        "native-country": "United-States",
    }
    print(model.predict(sample))