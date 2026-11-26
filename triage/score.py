"""Cost matrix from the hackathon brief.

Rows are the right verdict, columns are the verdict the system sent.
A missing case is treated as an escalation. Lower cost is better.
Escalating every case scores 0 verdict points: points measure the share of
that baseline cost which the system avoids.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# cost[truth][predicted]
COST = {
    "CLOSE": {"CLOSE": 0, "RFI": 3, "ESCALATE": 3},
    "RFI": {"CLOSE": 10, "RFI": 0, "ESCALATE": 2},
    "ESCALATE": {"CLOSE": 10, "RFI": 3, "ESCALATE": 0},
}


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def score_cases(expected: list[dict], predicted: list[dict]) -> dict:
    by_id = {row["case_id"]: row for row in predicted}
    details = []
    for truth in expected:
        got = by_id.get(truth["case_id"])
        predicted_verdict = got["verdict"] if got else "ESCALATE"
        cost = COST[truth["verdict"]][predicted_verdict]
        evidence_hit = None
        if got and truth.get("evidence"):
            have = set(got.get("evidence") or [])
            need = set(truth["evidence"])
            evidence_hit = len(need & have) / len(need) if need else 1.0
        code_hit = bool(got) and got.get("reason_code") == truth.get("reason_code")
        details.append({
            "case_id": truth["case_id"],
            "truth": truth["verdict"],
            "predicted": predicted_verdict,
            "cost": cost,
            "code_hit": code_hit,
            "evidence_recall": evidence_hit,
            "wrongful_close": truth["verdict"] != "CLOSE" and predicted_verdict == "CLOSE",
        })
    total = sum(item["cost"] for item in details)
    baseline = sum(COST[item["truth"]]["ESCALATE"] for item in details)
    if baseline <= 0:
        points = 100.0 if total == 0 else 0.0
    else:
        points = round(max(0.0, 100.0 * (1.0 - total / baseline)), 2)
    return {
        "cases": len(details),
        "cost": total,
        "escalate_all_cost": baseline,
        "verdict_points": points,
        "wrongful_closes": sum(1 for item in details if item["wrongful_close"]),
        "details": details,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="triage.score")
    parser.add_argument("--expected", required=True)
    parser.add_argument("--predicted", required=True)
    args = parser.parse_args(argv)
    result = score_cases(load_jsonl(Path(args.expected)), load_jsonl(Path(args.predicted)))
    print(json.dumps({key: result[key] for key in ("cases", "cost", "escalate_all_cost", "verdict_points", "wrongful_closes")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
