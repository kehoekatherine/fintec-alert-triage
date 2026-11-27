"""The published classical-model file must match the ranking rule."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ml_comparison_is_ranked_from_the_cost_matrix():
    data = json.loads((ROOT / "docs" / "data" / "ml_comparison.json").read_text())
    assert data["label_source"]["official_labels"] is False
    assert data["label_source"]["circular"] is True
    assert data["seed"] == 7
    assert data["folds"] == 5
    assert data["n_cases"] == 4000
    assert data["label_source"]["hand_label_count"] == 28
    assert [model["id"] for model in data["models"]] == ["logreg", "tree", "nb", "knn", "mlp"]

    def key(item):
        hand = item["hand"]
        return (-hand["fitness"], hand["unsafe_clearance_count"], 0 if item["id"] == "rules" else 1)

    rows = [data["rules_engine"]] + data["models"]
    assert data["winner"]["id"] == sorted(rows, key=key)[0]["id"]
    assert data["rules_engine"]["cv"] is None
    assert data["rules_engine"]["cites_evidence"] is True
    for model in data["models"]:
        assert model["cites_evidence"] is False
        assert model["cv"]["fitness"]["folds"]
        assert len(model["cv"]["fitness"]["folds"]) == 5
