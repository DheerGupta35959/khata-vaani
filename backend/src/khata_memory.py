"""SQLite-backed persistent memory for Khata-Vaani.

One table, one row per shopkeeper (user_id = LiveKit participant identity).
Profile fields are plain columns; the khata itself (customers + items) lives
in `facts` as JSON so the schema stays trivially simple.
"""

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Override with KHATA_DB_PATH env var for tests / custom locations.
DB_PATH = Path(
    os.environ.get("KHATA_DB_PATH", Path(__file__).resolve().parent.parent / "khata.db")
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    shop_name TEXT,
    language_preference TEXT,
    phone TEXT,
    call_consent INTEGER NOT NULL DEFAULT 0,
    facts TEXT NOT NULL DEFAULT '{}',
    last_interaction TEXT
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    phone TEXT NOT NULL,
    deadline TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS escalations (
    reference_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    who TEXT,
    what_happened TEXT NOT NULL,
    already_checked TEXT,
    urgency TEXT NOT NULL,
    language TEXT,
    preferred_followup TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    started_at TEXT,
    ended_at TEXT,
    channel TEXT,
    language TEXT,
    outcome TEXT,
    failure_type TEXT,
    escalation_created INTEGER NOT NULL DEFAULT 0
)
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _db():
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial CREATE TABLE (existing DBs)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "phone" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    if "call_consent" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN call_consent INTEGER NOT NULL DEFAULT 0"
        )


def init_db() -> None:
    with _db() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)


def lookup_caller(user_id: str) -> str:
    """Return the shopkeeper's saved profile + ledger as JSON text for the LLM."""
    init_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT name, shop_name, language_preference, phone, call_consent, facts "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        conn.execute(
            "UPDATE users SET last_interaction = ? WHERE user_id = ?",
            (_now_iso(), user_id),
        )
        if row is None:
            conn.execute(
                "INSERT INTO users (user_id, facts, last_interaction) VALUES (?, '{}', ?)",
                (user_id, _now_iso()),
            )
            return "This shopkeeper is new - no saved profile or ledger yet."

    facts = json.loads(row["facts"] or "{}")
    customers = facts.get("customers", {})
    return json.dumps(
        {
            "name": row["name"],
            "shop_name": row["shop_name"],
            "language_preference": row["language_preference"],
            "phone": row["phone"],
            "call_consent": bool(row["call_consent"]),
            "usual_items_sold": facts.get("usual_items_sold", []),
            "customers": sorted(
                (
                    {
                        "name": cname,
                        "udhaar_balance": cdata.get("udhaar_balance", 0),
                        "last_updated": cdata.get("last_updated"),
                    }
                    for cname, cdata in customers.items()
                ),
                key=lambda c: c["udhaar_balance"],
                reverse=True,
            ),
        },
        ensure_ascii=False,
    )


def remember_profile(user_id: str, *, name: str | None, shop_name: str | None) -> None:
    """Persist profile fields the shopkeeper shared out loud."""
    init_db()
    fields, values = [], []
    if name is not None:
        fields.append("name = ?")
        values.append(name)
    if shop_name is not None:
        fields.append("shop_name = ?")
        values.append(shop_name)
    if not fields:
        return
    fields.append("last_interaction = ?")
    values.append(_now_iso())
    values.append(user_id)
    with _db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, facts) VALUES (?, '{}')", (user_id,)
        )
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE user_id = ?", values)


def get_profile(user_id: str) -> dict:
    """Return the shopkeeper's raw profile + facts for tooling (no writes)."""
    init_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT name, shop_name, phone, call_consent, facts FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return {}
    facts = json.loads(row["facts"] or "{}")
    return {
        "name": row["name"],
        "shop_name": row["shop_name"],
        "phone": row["phone"],
        "call_consent": bool(row["call_consent"]),
        **facts,
    }


def save_customer_entry(user_id: str, name: str, amount: float, entry_type: str) -> str:
    """Update a customer's udhaar balance or log a sale. Returns a summary."""
    init_db()
    now = _now_iso()
    with _db() as conn:
        row = conn.execute(
            "SELECT facts FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (user_id, facts, last_interaction) VALUES (?, '{}', ?)",
                (user_id, now),
            )
            facts: dict[str, Any] = {}
        else:
            facts = json.loads(row["facts"] or "{}")

        customers = facts.setdefault("customers", {})
        items = facts.setdefault("usual_items_sold", [])

        if entry_type == "udhaar":
            key = name.strip().lower()
            cust = customers.setdefault(
                key, {"udhaar_balance": 0.0, "last_updated": now}
            )
            cust["udhaar_balance"] = round(
                cust.get("udhaar_balance", 0.0) + float(amount), 2
            )
            cust["last_updated"] = now
            saved = f"Udhaar saved: {name.strip()} now owes Rs. {cust['udhaar_balance']} in total."
        elif entry_type == "sale":
            items.append(name.strip())
            saved = f"Sale logged: {name.strip()} for Rs. {amount}."
        else:
            return f"Unknown entry_type '{entry_type}'. Use 'udhaar' or 'sale'."

        conn.execute(
            "UPDATE users SET facts = ?, last_interaction = ? WHERE user_id = ?",
            (json.dumps(facts, ensure_ascii=False), now, user_id),
        )
    return saved


def set_call_consent(user_id: str, consent: bool, phone: str | None = None) -> str:
    """Record explicit in-session consent to receive outbound reminder calls."""
    init_db()
    with _db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, facts) VALUES (?, '{}')", (user_id,)
        )
        conn.execute(
            "UPDATE users SET call_consent = ?, phone = COALESCE(?, phone), last_interaction = ? "
            "WHERE user_id = ?",
            (1 if consent else 0, phone, _now_iso(), user_id),
        )
    return "Consent to call recorded." if consent else "Consent to call revoked."


def has_call_consent(user_id: str) -> bool:
    """True only when the shopkeeper consented AND left a phone number."""
    init_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT call_consent, phone FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return bool(row and row["call_consent"] and row["phone"])


def schedule_reminder(user_id: str, deadline: str, reason: str) -> str:
    """Queue an outbound reminder call. Refuses without recorded consent + phone."""
    init_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT call_consent, phone FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not (row and row["call_consent"] and row["phone"]):
            return "No consent to call on record - reminder will surface next session instead."
        conn.execute(
            "INSERT INTO reminders (user_id, phone, deadline, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, row["phone"], deadline, reason, _now_iso()),
        )
    return "Reminder call scheduled."


def due_reminders(within_days: int = 7) -> list[dict]:
    """Pending reminders whose deadline is within `within_days` (or already past)."""
    init_db()
    cutoff = (datetime.now(timezone.utc) + timedelta(days=within_days)).isoformat(
        timespec="seconds"
    )
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, user_id, phone, deadline, reason, status FROM reminders "
            "WHERE status = 'pending' AND deadline <= ? ORDER BY deadline",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_reminder(reminder_id: int, status: str) -> None:
    """Update a reminder's status (called, opted_out, failed, skipped)."""
    init_db()
    with _db() as conn:
        conn.execute(
            "UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id)
        )


def latest_reminder(user_id: str) -> dict | None:
    """Return the most recent reminder row for a user, if any."""
    init_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT id, user_id, phone, deadline, reason, status FROM reminders "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "but",
    "or",
    "for",
    "to",
    "of",
    "on",
    "in",
    "me",
    "my",
    "i",
    "it",
    "about",
    "with",
    "they",
    "someone",
    "said",
}


def _tokens(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    return words - _STOPWORDS


def _similarity(a: str, b: str) -> float:
    """Jaccard token overlap between two texts."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def find_similar_open_escalation(user_id: str, what_happened: str) -> dict | None:
    """Return an open escalation with a similar what_happened, if any (dup check)."""
    init_db()
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM escalations WHERE user_id = ? AND status = 'open'",
            (user_id,),
        ).fetchall()
    best, best_score = None, 0.0
    for row in rows:
        score = _similarity(what_happened, row["what_happened"])
        if score > best_score:
            best, best_score = dict(row), score
    return best if best_score >= 0.5 else None


def save_escalation(row: dict) -> None:
    """Insert a new escalation or update an existing one (by reference_id)."""
    init_db()
    cols = [
        "reference_id",
        "user_id",
        "who",
        "what_happened",
        "already_checked",
        "urgency",
        "language",
        "preferred_followup",
        "status",
        "created_at",
    ]
    with _db() as conn:
        conn.execute(
            f"INSERT INTO escalations ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
            "ON CONFLICT(reference_id) DO UPDATE SET "
            + ", ".join(f"{c} = excluded.{c}" for c in cols if c != "reference_id"),
            [row.get(c) for c in cols],
        )


def start_call(call_id: str, channel: str, language: str) -> None:
    """Insert a call row with outcome unset; end_call fills it in."""
    init_db()
    with _db() as conn:
        conn.execute(
            "INSERT INTO calls (call_id, started_at, channel, language) VALUES (?, ?, ?, ?)",
            (call_id, _now_iso(), channel, language),
        )


def end_call(call_id: str, outcome: str, failure_type: str | None) -> None:
    """Close a call row with the derived outcome and failure type."""
    init_db()
    with _db() as conn:
        conn.execute(
            "UPDATE calls SET ended_at = ?, outcome = ?, failure_type = ? WHERE call_id = ?",
            (_now_iso(), outcome, failure_type, call_id),
        )


def set_call_escalation(call_id: str) -> None:
    """Mark that this call produced an escalation."""
    init_db()
    with _db() as conn:
        conn.execute(
            "UPDATE calls SET escalation_created = 1 WHERE call_id = ?", (call_id,)
        )


def call_stats() -> dict:
    """Aggregate dashboard numbers straight from the calls table."""
    init_db()
    with _db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        successful = conn.execute(
            "SELECT COUNT(*) FROM calls WHERE outcome = 'success'"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM calls WHERE outcome = 'failed'"
        ).fetchone()[0]
        breakdown = {
            row["failure_type"] or "null": row["n"]
            for row in conn.execute(
                "SELECT failure_type, COUNT(*) AS n FROM calls "
                "WHERE outcome = 'failed' GROUP BY failure_type"
            )
        }
    return {
        "total": total,
        "successful": successful,
        "failed": failed,
        "success_rate": round(successful / total * 100, 1) if total else 0,
        "failure_breakdown": breakdown,
    }


def recent_calls(limit: int = 20) -> list[dict]:
    """Most recent calls, newest first. No names, no amounts."""
    init_db()
    with _db() as conn:
        rows = conn.execute(
            "SELECT call_id, started_at, ended_at, channel, language, outcome, failure_type "
            "FROM calls ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    import tempfile

    DB_PATH = Path(tempfile.mkdtemp()) / "selfcheck.db"

    assert (
        lookup_caller("u1")
        == "This shopkeeper is new - no saved profile or ledger yet."
    )
    assert "owes Rs. 150" in save_customer_entry("u1", "Ram", 150, "udhaar")
    assert "owes Rs. 200" in save_customer_entry("u1", "Ram", 50, "udhaar")
    assert "logged" in save_customer_entry("u1", "hair oil", 120, "sale")
    remember_profile("u1", name="Ramesh", shop_name="Ramesh Kirana")
    led = lookup_caller("u1")
    assert '"name": "Ramesh"' in led
    assert '"shop_name": "Ramesh Kirana"' in led
    assert '"udhaar_balance": 200' in led
    assert "hair oil" in led
    # second call proves it persisted across connections
    assert lookup_caller("u1") == led
    # consent + reminders
    assert "Consent to call recorded." in set_call_consent(
        "u1", True, phone="+919876543210"
    )
    assert has_call_consent("u1")
    assert "Consent to call revoked." in set_call_consent("u1", False)
    assert not has_call_consent("u1")
    set_call_consent("u1", True, phone="+919876543210")
    assert "scheduled" in schedule_reminder("u1", "2026-08-31", "SVANidhi deadline")
    due = due_reminders(within_days=365)
    assert len(due) == 1 and due[0]["phone"] == "+919876543210"
    mark_reminder(due[0]["id"], "called")
    assert due_reminders(within_days=365) == []
    # no consent -> refuses to schedule
    set_call_consent("u1", False)
    assert "No consent" in schedule_reminder("u1", "2026-08-31", "SVANidhi deadline")
    # escalations + dup prevention
    now = _now_iso()
    save_escalation(
        {
            "reference_id": "KV-1234",
            "user_id": "u1",
            "who": "Ramesh",
            "what_happened": "caller says a bank officer asked for his OTP",
            "already_checked": "told him not to share it",
            "urgency": "high",
            "language": "unknown",
            "preferred_followup": "call back",
            "status": "open",
            "created_at": now,
        }
    )
    dup = find_similar_open_escalation("u1", "a bank officer asked for my OTP number")
    assert dup and dup["reference_id"] == "KV-1234"
    assert find_similar_open_escalation("u1", "the roof is leaking in the shop") is None
    save_escalation(
        {
            "reference_id": "KV-1234",
            "user_id": "u1",
            "who": "Ramesh",
            "what_happened": "updated: bank officer asked for OTP again",
            "already_checked": "warned again",
            "urgency": "emergency",
            "language": "unknown",
            "preferred_followup": "call back",
            "status": "open",
            "created_at": now,
        }
    )
    # call analytics
    start_call("call-0001", "browser", "unknown")
    end_call("call-0001", "success", None)
    start_call("call-0002", "sip", "hindi")
    end_call("call-0002", "failed", "no_response")
    stats = call_stats()
    assert stats["total"] == 2
    assert stats["successful"] == 1
    assert stats["failed"] == 1
    assert stats["success_rate"] == 50.0
    assert stats["failure_breakdown"]["no_response"] == 1
    recent = recent_calls(limit=5)
    by_id = {r["call_id"]: r for r in recent}
    assert by_id["call-0002"]["outcome"] == "failed"
    assert by_id["call-0001"]["outcome"] == "success"
    set_call_escalation("call-0002")
    print(f"self-check OK -> {DB_PATH}")
