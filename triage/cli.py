"""Command line for one institution folder."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .loader import load_institution
from .engine import triage_bundle


def dumps_line(decision: dict) -> str:
    ordered = {
        "case_id": decision["case_id"],
        "alert_ids": decision["alert_ids"],
        "verdict": decision["verdict"],
        "reason_code": decision["reason_code"],
        "reason": decision["reason"],
        "evidence": decision["evidence"],
        "action": decision.get("action") or "",
    }
    return json.dumps(ordered, ensure_ascii=False, separators=(", ", ": "))


def write_output(path: Path, decisions: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(dumps_line(item) for item in decisions)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def distribution(decisions: list[dict]) -> str:
    verdicts = Counter(item["verdict"] for item in decisions)
    codes = Counter(item["reason_code"] for item in decisions)
    lines = [f"{sum(verdicts.values())} cases", "verdicts: " + ", ".join(f"{key} {verdicts[key]}" for key in ("CLOSE", "RFI", "ESCALATE"))]
    for code, count in codes.most_common():
        lines.append(f"  {count:5} {code}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="triage",
        description="ClearPath AML, the Alert Triage Assistant. Reviews transaction-monitoring alerts against the firm's policy and writes CLOSE, RFI or ESCALATE with cited evidence.",
    )
    parser.add_argument("--cases", required=True, help="Institution folder containing cases.jsonl")
    parser.add_argument("--out", required=True, help="Output JSONL path")
    parser.add_argument("--stats", action="store_true", help="Print verdict counts to stderr")
    parser.add_argument("--llm", action="store_true", help="Optional OpenRouter pass. Off unless set. Rules remain the floor.")
    args = parser.parse_args(argv)
    bundle = load_institution(args.cases)
    decisions = triage_bundle(bundle)
    if args.llm:
        from .llm import apply_llm
        decisions = apply_llm(bundle, decisions)
    write_output(Path(args.out), decisions)
    if args.stats:
        print(distribution(decisions), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
