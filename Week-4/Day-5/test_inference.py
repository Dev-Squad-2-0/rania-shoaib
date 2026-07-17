import pytest
import pandas as pd
from inference import IncomeModel, InferenceInputError

VALID_ROW = {
    "age": 45, "workclass": "Private", "education-num": 13,
    "marital-status": "Married-civ-spouse", "occupation": "Exec-managerial",
    "relationship": "Husband", "race": "White", "sex": "Male",
    "capital-gain": 5000, "capital-loss": 0, "hours-per-week": 50,
    "native-country": "United-States",
}

@pytest.fixture(scope="module")
def model():
    return IncomeModel()

def test_valid_single_row_returns_expected_columns(model):
    result = model.predict(VALID_ROW)
    assert list(result.columns) == ["probability", "predicted_class", "top_3_features"]
    assert 0.0 <= result["probability"].iloc[0] <= 1.0
    assert result["predicted_class"].iloc[0] in (0, 1)
    assert len(result["top_3_features"].iloc[0]) == 3

def test_missing_column_raises(model):
    bad_row = VALID_ROW.copy()
    del bad_row["marital-status"]
    with pytest.raises(InferenceInputError, match="Missing required columns"):
        model.predict(bad_row)

def test_unseen_category_does_not_crash(model):
    # OneHotEncoder(handle_unknown="ignore") should absorb this silently
    weird_row = VALID_ROW.copy()
    weird_row["native-country"] = "Atlantis"          # not in training data
    weird_row["workclass"] = "Freelance-Astronaut"     # not in training data
    result = model.predict(weird_row)
    assert result["predicted_class"].iloc[0] in (0, 1)

def test_non_numeric_age_raises(model):
    bad_row = VALID_ROW.copy()
    bad_row["age"] = "not-a-number"
    with pytest.raises(InferenceInputError, match="non-numeric"):
        model.predict(bad_row)

def test_csv_input(model, tmp_path):
    csv_path = tmp_path / "batch.csv"
    pd.DataFrame([VALID_ROW, VALID_ROW]).to_csv(csv_path, index=False)
    result = model.predict(str(csv_path))
    assert len(result) == 2

def test_batch_dict_list(model):
    result = model.predict([VALID_ROW, VALID_ROW])
    assert len(result) == 2