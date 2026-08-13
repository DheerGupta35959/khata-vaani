"""SQLite-backed persistent memory for Khata-Vaani.

One table, one row per shopkeeper (user_id = LiveKit participant identity).
Profile fields are plain columns; the khata itself (customers + items) lives
in `facts` as JSON so the schema stays trivially simple.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Override with KHATA_DB_PATH env var for tests / custom locations.
DB_PATH = Path(os.environ.get("KHATA_DB_PATH", Path(__file__).resolve().parent.parent / "khata.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    shop_name TEXT,
    language_preference TEXT,
    facts TEXT NOT NULL DEFAULT '{}',
    last_interaction TEXT
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


def init_db() -> None:
    with _db() as conn:
        conn.execute(_SCHEMA)


def lookup_caller(user_id: str) -> str:
    """Return the shopkeeper's saved profile + ledger as JSON text for the LLM."""
    init_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT name, shop_name, language_preference, facts FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        conn.execute(
            "UPDATE users SET last_interaction = ? WHERE user_id = ?", (_now_iso(), user_id)
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
        conn.execute("INSERT OR IGNORE INTO users (user_id, facts) VALUES (?, '{}')", (user_id,))
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE user_id = ?", values)


def get_profile(user_id: str) -> dict:
    """Return the shopkeeper's raw profile + facts for tooling (no writes)."""
    init_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT name, shop_name, facts FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        return {}
    facts = json.loads(row["facts"] or "{}")
    return {"name": row["name"], "shop_name": row["shop_name"], **facts}


def save_customer_entry(user_id: str, name: str, amount: float, entry_type: str) -> str:
    """Update a customer's udhaar balance or log a sale. Returns a summary."""
    init_db()
    now = _now_iso()
    with _db() as conn:
        row = conn.execute("SELECT facts FROM users WHERE user_id = ?", (user_id,)).fetchone()
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
            cust = customers.setdefault(key, {"udhaar_balance": 0.0, "last_updated": now})
            cust["udhaar_balance"] = round(cust.get("udhaar_balance", 0.0) + float(amount), 2)
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


if __name__ == "__main__":
    import tempfile

    DB_PATH = Path(tempfile.mkdtemp()) / "selfcheck.db"

    assert lookup_caller("u1") == "This shopkeeper is new - no saved profile or ledger yet."
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
    print(f"self-check OK -> {DB_PATH}")

