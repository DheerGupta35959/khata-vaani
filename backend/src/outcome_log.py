"""Append-only JSONL log of outbound call outcomes."""

import json
from datetime import datetime
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "outbound_calls.jsonl"


def log_call(meta: dict, outcome: str) -> None:
    """Append one line per outbound call."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "outcome": outcome,
        "user_id": meta.get("user_id"),
        "phone": meta.get("phone_number"),
        "reminder_id": meta.get("reminder_id"),
        "deadline": meta.get("deadline"),
        "reason": meta.get("reason"),
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
