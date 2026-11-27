"""Compare classical models with the rules engine.

The development cases have no official labels. This script trains logistic
regression, a decision tree, Gaussian naive Bayes, k-nearest neighbours and a
small neural network to reproduce the rules engine's verdicts, using stratified
5-fold cross-validation with a fixed seed. It then refits each model on every
development case except the 28 hand-labelled ones and scores those 28 against
the hand labels.

Training on the rules engine's own output is circular. The hand labels were
read from the same policy and, in this repository, agree with the engine on
every case. A high score means the model copied the policy from the features.
It is not an official unseen-test score.

Run from the repository root:

    python3 -m pip install scikit-learn
    python3 analysis/ml_compare.py
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier
from sklearn.model_selection import StratifiedKFold

ROOT = Path(__file__).resolve().parents[1]
INSTITUTIONS = ("bank", "emoney", "investment", "payments_fx")
LABELS = ("CLOSE", "RFI", "ESCALATE")
RULES = ("TM01", "TM02", "TM03", "TM04", "TM05", "TM06", "TM07", "TM08", "TM10", "TM22")
HIGH_RISK = {
    "afghanistan", "belarus", "democratic republic of the congo", "haiti", "iran", "iraq",
    "lebanon", "libya", "mali", "myanmar", "north korea", "russia", "somalia", "south sudan",
    "syria", "venezuela", "yemen",
}
COST = {
    "CLOSE": {"CLOSE": 0, "RFI": 3, "ESCALATE": 3},
    "RFI": {"CLOSE": 10, "RFI": 0, "ESCALATE": 2},
    "ESCALATE": {"CLOSE": 10, "RFI": 3, "ESCALATE": 0},
}
SEED = 7
FOLDS = 5
# Student t critical value, two-sided 95%, df = 4. Used only to turn five fold
# scores into an interval. It is not a bootstrap of customers.
T_95_DF4 = 2.7764451051977987


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                rows.append(json.loads(text))
    return rows


def norm(value: str | None) -> str:
    return " ".join(str(value or "").lower().split())


def load_cases() -> list[dict]:
    rows = []
    for institution in INSTITUTIONS:
        root = ROOT / "institutions" / institution
        customers = {row["customer_id"]: row for row in read_jsonl(root / "customers.jsonl")}
        alerts = defaultdict(list)
        for alert in read_jsonl(root / "alerts.jsonl"):
            alerts[alert.get("case_id")].append(alert)
        txs = defaultdict(list)
        for tx in read_jsonl(root / "transactions.jsonl"):
            txs[tx.get("customer_id")].append(tx)
        docs = defaultdict(list)
        for doc in read_jsonl(root / "evidence.jsonl"):
            docs[doc.get("customer_id")].append(doc)
        reviews = defaultdict(int)
        for review in read_jsonl(root / "previous_reviews.jsonl"):
            reviews[review.get("customer_id")] += 1
        listed = {norm(row.get("name")) for row in read_jsonl(root / "internal_list.jsonl")}
        listed.discard("")
        labels = {row["case_id"]: row["verdict"] for row in read_jsonl(ROOT / "output" / f"{institution}.jsonl")}
        for case in read_jsonl(root / "cases.jsonl"):
            rows.append({
                "institution": institution,
                "case": case,
                "customer": customers.get(case.get("customer_id"), {}),
                "alerts": alerts.get(case["case_id"], []),
                "transactions": txs.get(case.get("customer_id"), []),
                "documents": docs.get(case.get("customer_id"), []),
                "n_reviews": reviews.get(case.get("customer_id"), 0),
                "listed": listed,
                "label": labels[case["case_id"]],
            })
    return rows


def in_window(tx: dict, start: str, end: str) -> bool:
    booked = str(tx.get("booked_at") or "")[:10]
    return bool(booked) and start <= booked <= end


def feature_names() -> list[str]:
    names = [f"inst_{name}" for name in INSTITUTIONS]
    names += [
        "is_business", "risk_medium", "log_monthly_capacity", "held_payment",
        "n_alerts", "n_reviews", "n_documents", "window_days",
        "n_tx", "n_in", "n_out", "log_sum_in", "log_sum_out", "log_max",
        "out_in_ratio", "n_counterparties", "n_countries", "n_cash", "log_cash",
        "n_high_risk", "n_undeclared_high_risk", "n_near_threshold",
        "listed_counterparty", "listed_customer",
        "doc_invoice", "doc_statement", "doc_letter", "doc_register",
        "inflow_over_capacity",
    ]
    names += [f"alert_{rule}" for rule in RULES]
    return names


def featurise(row: dict) -> list[float]:
    case = row["case"]
    customer = row["customer"]
    declared = customer.get("declared") or {}
    window = case.get("review_window") or {}
    start = str(window.get("from") or "")
    end = str(window.get("to") or "")
    expected = {norm(country) for country in (declared.get("expected_countries") or [])}
    income = declared.get("monthly_income_gbp")
    turnover = declared.get("annual_turnover_gbp")
    capacity = float(income or 0)
    if not capacity and turnover:
        capacity = float(turnover) / 12.0
    txs = [tx for tx in row["transactions"] if in_window(tx, start, end)]
    sum_in = sum_out = max_amount = cash = 0.0
    n_in = n_out = n_cash = n_high = n_undeclared = n_near = 0
    parties = set()
    countries = set()
    listed_party = 0
    for tx in txs:
        amount = float(tx.get("amount_gbp") or 0)
        max_amount = max(max_amount, amount)
        inbound = str(tx.get("direction") or "").lower() in {"in", "credit", "inbound"}
        if inbound:
            n_in += 1
            sum_in += amount
        else:
            n_out += 1
            sum_out += amount
        channel = str(tx.get("channel") or "")
        if "cash" in channel:
            n_cash += 1
            cash += amount
        country = norm(tx.get("counterparty_country"))
        if country:
            countries.add(country)
            if country in HIGH_RISK:
                n_high += 1
                if country not in expected:
                    n_undeclared += 1
        party = norm(tx.get("counterparty_name"))
        if party:
            parties.add(party)
            if party in row["listed"]:
                listed_party = 1
        if (900 <= amount < 1000) or (9000 <= amount < 10000):
            n_near += 1
    alert_ids = {alert.get("rule_id") for alert in row["alerts"]}
    doc_blob = " ".join(f"{doc.get('type') or ''} {doc.get('title') or ''}" for doc in row["documents"]).lower()
    try:
        window_days = (np.datetime64(end) - np.datetime64(start)) / np.timedelta64(1, "D")
        window_days = float(window_days) if np.isfinite(window_days) else 0.0
    except Exception:
        window_days = 0.0
    values = [1.0 if row["institution"] == name else 0.0 for name in INSTITUTIONS]
    values += [
        1.0 if customer.get("kind") == "business" else 0.0,
        1.0 if norm(customer.get("risk_rating")) == "medium" else 0.0,
        math.log1p(capacity),
        1.0 if case.get("held_payment_id") else 0.0,
        float(len(row["alerts"])),
        float(row["n_reviews"]),
        float(len(row["documents"])),
        window_days,
        float(len(txs)),
        float(n_in),
        float(n_out),
        math.log1p(sum_in),
        math.log1p(sum_out),
        math.log1p(max_amount),
        sum_out / (sum_in + 1.0),
        float(len(parties)),
        float(len(countries)),
        float(n_cash),
        math.log1p(cash),
        float(n_high),
        float(n_undeclared),
        float(n_near),
        float(listed_party),
        1.0 if norm(customer.get("name")) in row["listed"] else 0.0,
        1.0 if "invoice" in doc_blob else 0.0,
        1.0 if "statement" in doc_blob else 0.0,
        1.0 if "letter" in doc_blob else 0.0,
        1.0 if "register" in doc_blob else 0.0,
        (sum_in / capacity) if capacity else 0.0,
    ]
    values += [1.0 if rule in alert_ids else 0.0 for rule in RULES]
    return values


def round4(value):
    if value is None:
        return None
    return round(float(value), 4)


def score_vectors(truth: list[str], pred: list[str]) -> dict:
    index = {label: i for i, label in enumerate(LABELS)}
    matrix = [[0, 0, 0] for _ in LABELS]
    for true, guessed in zip(truth, pred):
        matrix[index[true]][index[guessed]] += 1
    support = [sum(row) for row in matrix]
    predicted = [sum(matrix[i][j] for i in range(3)) for j in range(3)]
    total = sum(support)
    correct = sum(matrix[i][i] for i in range(3))
    per_class = {}
    f1s = []
    for i, label in enumerate(LABELS):
        precision = matrix[i][i] / predicted[i] if predicted[i] else None
        recall = matrix[i][i] / support[i] if support[i] else None
        if precision is None or recall is None or precision + recall == 0:
            f1 = None
        else:
            f1 = 2 * precision * recall / (precision + recall)
        if f1 is not None:
            f1s.append(f1)
        per_class[label] = {
            "precision": round4(precision),
            "recall": round4(recall),
            "f1": round4(f1),
            "support": support[i],
        }
    cost = 0
    baseline = 0
    for i, true in enumerate(LABELS):
        baseline += support[i] * COST[true]["ESCALATE"]
        for j, guessed in enumerate(LABELS):
            cost += matrix[i][j] * COST[true][guessed]
    fitness = 100.0 if baseline <= 0 else max(0.0, 100.0 * (1.0 - cost / baseline))
    non_close = support[1] + support[2]
    return {
        "n": total,
        "accuracy": round4(correct / total if total else None),
        "macro_f1": round4(sum(f1s) / len(LABELS) if f1s else None),
        "per_class": per_class,
        "confusion_matrix": {
            "rows": list(LABELS),
            "columns": list(LABELS),
            "counts": matrix,
        },
        "cost": cost,
        "escalate_all_cost": baseline,
        "fitness": round(fitness, 2),
        "unsafe_clearance_count": matrix[1][0] + matrix[2][0],
        "unsafe_clearance_rate": round4((matrix[1][0] + matrix[2][0]) / non_close if non_close else None),
        "over_caution_count": support[0] - matrix[0][0],
        "over_caution_rate": round4((support[0] - matrix[0][0]) / support[0] if support[0] else None),
        "tipping_off_count": matrix[2][1],
        "tipping_off_rate": round4(matrix[2][1] / support[2] if support[2] else None),
    }


def mean_std(values: list[float], digits: int = 4) -> dict:
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    half = T_95_DF4 * std / math.sqrt(len(arr)) if len(arr) > 1 else 0.0

    def rnd(value):
        return round(float(value), digits)

    return {
        "mean": rnd(mean),
        "fold_std": rnd(std),
        "folds": [rnd(value) for value in values],
        "interval_95": [rnd(mean - half), rnd(mean + half)],
        "interval_note": "Mean plus or minus the Student t 95% critical value (df=4) times the fold standard deviation over sqrt(5). It describes the five fold scores, not a bootstrap of customers.",
    }


def make_models() -> list[tuple[str, str, object, str]]:
    return [
        ("logreg", "Logistic regression", Pipeline([
            ("scale", StandardScaler()),
            ("model", LogisticRegression(max_iter=500, class_weight="balanced", random_state=SEED)),
        ]), "Coefficients on scaled features. A regulator can read the direction of a feature, not the case narrative."),
        ("tree", "Decision tree", DecisionTreeClassifier(
            max_depth=8, min_samples_leaf=20, class_weight="balanced", random_state=SEED,
        ), "A short list of feature splits. Readable, but the splits are not the policy text and they do not cite a document."),
        ("nb", "Gaussian naive Bayes", Pipeline([
            ("scale", StandardScaler()),
            ("model", GaussianNB()),
        ]), "Assumes each feature is independent given the verdict. The class-conditional means can be listed. That assumption is false for these cases."),
        ("knn", "K-nearest neighbours", Pipeline([
            ("scale", StandardScaler()),
            ("model", KNeighborsClassifier(n_neighbors=15, weights="uniform")),
        ]), "The verdict is a vote of the 15 nearest training cases. There is no rule to show a regulator, and no evidence id."),
        ("mlp", "Neural network (MLP)", Pipeline([
            ("scale", StandardScaler()),
            ("model", MLPClassifier(hidden_layer_sizes=(32,), max_iter=300, random_state=SEED)),
        ]), "One hidden layer of 32 units. The weights are not an explanation and the model cites no document."),
    ]


def fit_predict(model, x_train, y_train, x_test):
    started = time.perf_counter()
    model.fit(x_train, y_train)
    fit_seconds = time.perf_counter() - started
    started = time.perf_counter()
    pred = model.predict(x_test)
    predict_seconds = time.perf_counter() - started
    return pred, fit_seconds, predict_seconds


def cross_validate(name, title, model, explain, x, y) -> dict:
    splitter = StratifiedKFold(n_splits=FOLDS, shuffle=True, random_state=SEED)
    fold_rows = []
    pooled_true: list[str] = []
    pooled_pred: list[str] = []
    for fold, (train_idx, test_idx) in enumerate(splitter.split(x, y), start=1):
        estimator = make_estimator(name)
        pred, fit_seconds, predict_seconds = fit_predict(estimator, x[train_idx], y[train_idx], x[test_idx])
        train_pred = estimator.predict(x[train_idx])
        held = score_vectors(y[test_idx].tolist(), pred.tolist())
        trained = score_vectors(y[train_idx].tolist(), train_pred.tolist())
        fold_rows.append({
            "fold": fold,
            "n_test": int(len(test_idx)),
            "fit_seconds": round(fit_seconds, 4),
            "predict_seconds": round(predict_seconds, 6),
            "predict_seconds_per_case": round(predict_seconds / len(test_idx), 8),
            "test": held,
            "train_accuracy": trained["accuracy"],
            "train_fitness": trained["fitness"],
            "overfit_accuracy_gap": round4(trained["accuracy"] - held["accuracy"]),
            "overfit_fitness_gap": round(trained["fitness"] - held["fitness"], 2),
        })
        pooled_true.extend(y[test_idx].tolist())
        pooled_pred.extend(pred.tolist())
    pooled = score_vectors(pooled_true, pooled_pred)
    return {
        "id": name,
        "label": title,
        "explainability": explain,
        "cites_evidence": False,
        "deterministic": False,
        "cv": {
            "pooled": pooled,
            "fitness": mean_std([row["test"]["fitness"] for row in fold_rows]),
            "accuracy": mean_std([row["test"]["accuracy"] for row in fold_rows]),
            "macro_f1": mean_std([row["test"]["macro_f1"] for row in fold_rows]),
            "unsafe_clearance_rate": mean_std([row["test"]["unsafe_clearance_rate"] for row in fold_rows]),
            "overfit_fitness_gap": mean_std([row["overfit_fitness_gap"] for row in fold_rows]),
            "predict_seconds_per_case": mean_std([row["predict_seconds_per_case"] for row in fold_rows], digits=8),
            "folds": fold_rows,
        },
    }


def make_estimator(name: str):
    return {item[0]: item[2] for item in make_models()}[name]


def hand_eval(name, x, y, ids, hand_ids) -> dict:
    mask = np.array([case_id not in hand_ids for case_id in ids])
    test = np.array([case_id in hand_ids for case_id in ids])
    estimator = make_estimator(name)
    pred, fit_seconds, predict_seconds = fit_predict(estimator, x[mask], y[mask], x[test])
    order = [ids[i] for i in np.flatnonzero(test)]
    return {
        "fit_seconds": round(fit_seconds, 4),
        "predict_seconds": round(predict_seconds, 4),
        "predict_seconds_per_case": round(predict_seconds / max(1, int(test.sum())), 6),
        "trained_on": int(mask.sum()),
        "predictions": {case_id: verdict for case_id, verdict in zip(order, pred.tolist())},
    }


def main() -> None:
    started = time.perf_counter()
    raw = load_cases()
    names = feature_names()
    x = np.asarray([featurise(row) for row in raw], dtype=float)
    y = np.asarray([row["label"] for row in raw])
    ids = [row["case"]["case_id"] for row in raw]
    if x.shape[1] != len(names):
        raise SystemExit(f"Feature width {x.shape[1]} does not match {len(names)} names")
    hands = json.loads((ROOT / "docs" / "data" / "hand_labels.json").read_text())
    hand_ids = {row["case_id"] for row in hands["labels"]}
    hand_truth = {row["case_id"]: row["verdict"] for row in hands["labels"]}
    engine_on_hand = {row["case_id"]: row["engine_verdict"] for row in hands["labels"]}
    missing = sorted(hand_ids - set(ids))
    if missing:
        raise SystemExit(f"Hand-labelled cases missing from the feature table: {missing}")
    agrees = sum(1 for row in hands["labels"] if row["verdict"] == row["engine_verdict"])

    counts = {label: int((y == label).sum()) for label in LABELS}
    models = []
    for name, title, _estimator, explain in make_models():
        print(f"cv {name}", flush=True)
        result = cross_validate(name, title, None, explain, x, y)
        print(f"hand {name}", flush=True)
        held = hand_eval(name, x, y, ids, hand_ids)
        ordered_ids = [case_id for case_id in ids if case_id in hand_ids]
        truth = [hand_truth[case_id] for case_id in ordered_ids]
        pred = [held["predictions"][case_id] for case_id in ordered_ids]
        result["hand"] = score_vectors(truth, pred)
        result["hand"]["trained_on"] = held["trained_on"]
        result["hand"]["fit_seconds"] = held["fit_seconds"]
        result["hand"]["predict_seconds_per_case"] = held["predict_seconds_per_case"]
        result["marginal_cost_gbp"] = 0
        models.append(result)

    rules_truth = [hand_truth[row["case_id"]] for row in hands["labels"]]
    rules_pred = [engine_on_hand[row["case_id"]] for row in hands["labels"]]
    rules_hand = score_vectors(rules_truth, rules_pred)
    payload = json.loads((ROOT / "docs" / "data" / "payload_stats.json").read_text())
    rules = {
        "id": "rules",
        "label": "Rules engine",
        "explainability": "The verdict is a written rule from the firm's policy. The output cites the document, transaction or list id it used.",
        "cites_evidence": True,
        "deterministic": True,
        "marginal_cost_gbp": 0,
        "predict_seconds_per_case_measured": payload["triage_seconds_per_case"],
        "hand": rules_hand,
        "cv": None,
        "cv_note": "The rules engine is the source of the training labels. Scoring it against those labels would be exactly 100 with zero variance. That figure is not reported as a cross-validated accuracy.",
    }

    def rank(item):
        # Fitness is the cost-matrix score, so a wrongful close already costs 10.
        # Unsafe closes break a fitness tie. The rules engine breaks any remaining tie
        # because it cites evidence and does not depend on a seed.
        return (
            -item["hand"]["fitness"],
            item["hand"]["unsafe_clearance_count"],
            0 if item["id"] == "rules" else 1,
        )

    contenders = [rules] + models
    winner = sorted(contenders, key=rank)[0]
    runner = sorted([item for item in contenders if item["id"] != winner["id"]], key=rank)[0]
    margins = []
    for item in contenders:
        margins.append({
            "id": item["id"],
            "label": item["label"],
            "hand_fitness": item["hand"]["fitness"],
            "hand_fitness_behind_winner": round(winner["hand"]["fitness"] - item["hand"]["fitness"], 2),
            "unsafe_clearance_count": item["hand"]["unsafe_clearance_count"],
            "unsafe_clearance_behind_winner": item["hand"]["unsafe_clearance_count"] - winner["hand"]["unsafe_clearance_count"],
            "cv_fitness_mean": None if item["cv"] is None else item["cv"]["fitness"]["mean"],
            "cv_fitness_fold_std": None if item["cv"] is None else item["cv"]["fitness"]["fold_std"],
            "policy_reproduction_gap": None if item["cv"] is None else round(100 - item["cv"]["fitness"]["mean"], 2),
        })

    reason = (
        f"{winner['label']} is first on the cost-matrix fitness of the 28 hand labels "
        f"({winner['hand']['fitness']}), with {winner['hand']['unsafe_clearance_count']} unsafe closes. "
        "Fitness already charges 10 for a wrongful close, so raw accuracy is not the deciding figure. "
    )
    if winner["id"] == "rules":
        reason += (
            "It encodes the policy directly, cites the evidence id on each case, and does not vary between runs. "
            f"The next system, {runner['label']}, is {round(winner['hand']['fitness'] - runner['hand']['fitness'], 2)} "
            f"fitness points lower on the same 28 cases and has {runner['hand']['unsafe_clearance_count']} unsafe "
            f"{'close' if runner['hand']['unsafe_clearance_count'] == 1 else 'closes'}. "
            "The classical models were trained to copy the engine, so that gap is a failure to reproduce the policy from these features, "
            "not a score against an official answer key."
        )
    else:
        reason += (
            f"It beat the rules engine on unsafe closes or cost-matrix fitness on these 28 cases. "
            f"The rules engine scored fitness {rules_hand['fitness']} with {rules_hand['unsafe_clearance_count']} unsafe closes. "
            "This is still not an official unseen score."
        )

    payload_out = {
        "seed": SEED,
        "folds": FOLDS,
        "n_cases": len(raw),
        "n_features": len(names),
        "feature_names": names,
        "class_counts_rules_labels": counts,
        "label_source": {
            "official_labels": False,
            "training_labels": "Rules-engine verdicts in output/*.jsonl, produced by triage/engine.py from each firm's POLICY.md.",
            "hand_labels": "docs/data/hand_labels.json, 28 cases read against POLICY.md.",
            "hand_labels_matching_engine": agrees,
            "hand_label_count": len(hands["labels"]),
            "circular": True,
        },
        "definitions": {
            "fitness": "100 times the share of the escalate-everything cost the system avoids. The cost matrix is CLOSE/RFI/ESCALATE as in triage/score.py.",
            "unsafe_clearance": "A true RFI or ESCALATE predicted as CLOSE. Count and rate use the non-close cases as the denominator.",
            "over_caution": "A true CLOSE predicted as RFI or ESCALATE.",
            "tipping_off": "A true ESCALATE predicted as RFI. The rules engine's own action text tells the analyst not to contact the customer; a classical model does not produce that warning.",
            "overfit_gap": "Train-fold score minus held-out fold score. Positive means the training fold looked better than the cases it did not fit.",
            "cited_evidence": "Whether the system names a document, transaction or list id. Classical models do not.",
        },
        "caveats": [
            "There is no official label file in the starter kit. Do not read these figures as the hackathon score on the 800 unseen cases.",
            "Cross-validation trains and tests on the rules engine's own verdicts. A perfect score would mean the model reproduced the policy, not that the policy is correct.",
            f"The 28 hand labels agree with the engine on {agrees} of {len(hands['labels'])} cases. Scoring a model against them, after training it to copy the engine, is not an independent test.",
            "Features are amounts, counts, ratios, alert ids, institution, and name flags. They do not include the wording of the evidence documents, which is what the rules engine reads before it closes a case.",
            "Fold intervals use the five fold scores and a Student t critical value. They are not a confidence interval for a new customer.",
            "Latency for the classical models is this machine's predict time. The rules-engine latency is the figure already measured in docs/data/payload_stats.json. Neither includes a network call.",
            "Marginal token cost of every classical model and of the rules engine is £0. That is not a claim about analyst time.",
        ],
        "rules_engine": rules,
        "models": models,
        "winner": {
            "id": winner["id"],
            "label": winner["label"],
            "reason": reason,
            "tie_break": "Higher hand-label fitness first, because fitness is the cost-matrix score and a wrongful close already costs 10. Fewer unsafe closes break a fitness tie. The rules engine wins any remaining tie.",
        },
        "margins": margins,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }
    # Drop the bulky per-case prediction maps from the published file. The scores remain.
    for model in payload_out["models"]:
        model.pop("predictions", None)
        model["hand"].pop("predictions", None)
    out = ROOT / "docs" / "data" / "ml_comparison.json"
    out.write_text(json.dumps(payload_out, indent=2) + "\n", encoding="utf-8")
    print("wrote", out, "winner", winner["label"], "elapsed", payload_out["elapsed_seconds"])


if __name__ == "__main__":
    main()
