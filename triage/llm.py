"""Optional OpenRouter pass. Off unless --llm is set.

Temperature is 0 and responses are cached under system/.cache so a repeated
run does not change. The rules verdict is a floor: the model cannot turn an
escalation into an RFI or a close, and cannot turn an RFI into a close.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path

_SEVERITY = {"CLOSE": 0, "RFI": 1, "ESCALATE": 2}


def _cache_path(key: str) -> Path:
    root = Path(__file__).resolve().parents[1] / "system" / ".cache"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{key}.json"


def _allowed(rules: dict, proposed: dict) -> bool:
    if proposed.get("verdict") not in _SEVERITY or not proposed.get("reason_code"):
        return False
    if _SEVERITY[proposed["verdict"]] < _SEVERITY[rules["verdict"]]:
        return False
    known = set(rules.get("evidence") or [])
    # Proposed evidence must be a subset of ids the rules already judged real,
    # plus anything the caller validated. The rules evidence is the safe set
    # when the model invents an id.
    for item in proposed.get("evidence") or []:
        if item not in known and item not in set(rules.get("_known") or []):
            return False
    return True


def apply_llm(bundle: dict, decisions: list[dict]) -> list[dict]:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return decisions
    updated = []
    for decision in decisions:
        digest = hashlib.sha256(json.dumps(decision, sort_keys=True).encode()).hexdigest()
        cache = _cache_path(digest)
        if cache.exists():
            updated.append(json.loads(cache.read_text(encoding="utf-8")))
            continue
        proposed = _call(key, decision)
        chosen = proposed if proposed and _allowed(decision, proposed) else decision
        cache.write_text(json.dumps(chosen), encoding="utf-8")
        updated.append(chosen)
    return updated


def _call(key: str, decision: dict) -> dict | None:
    payload = {
        "model": os.environ.get("TRIAGE_LLM_MODEL", "openai/gpt-4o-mini"),
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You revise transaction-monitoring triage wording. "
                    "Return JSON with verdict, reason_code, reason, evidence. "
                    "Do not lower the severity of the given verdict."
                ),
            },
            {"role": "user", "content": json.dumps(decision)},
        ],
    }
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode())
        content = body["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception:
        return None
