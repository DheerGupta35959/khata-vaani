"""Trigger outbound Khata-Vaani reminder calls via LiveKit dispatch.

Usage:
  python src/run_outbound.py seed-consent --user <id> --phone <E.164>
  python src/run_outbound.py call --user <id> [--deadline YYYY-MM-DD] [--reason TEXT]
  python src/run_outbound.py due [--within N]

The agent refuses to dial unless consent + phone are on record (set via
seed-consent, or in-session via set_call_consent). `call` schedules a reminder
(which re-checks consent) and dispatches an agent to dial it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

import khata_memory

load_dotenv(".env.local")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("run_outbound")

AGENT_NAME = "my-agent"


def _need(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"[ERROR] {name} not set in .env.local")
    return value


async def _dispatch(meta: dict) -> None:
    from livekit import api as lk_api

    lk = lk_api.LiveKitAPI(
        url=_need("LIVEKIT_URL"),
        api_key=_need("LIVEKIT_API_KEY"),
        api_secret=_need("LIVEKIT_API_SECRET"),
    )
    try:
        room_name = f"khata-call-{uuid.uuid4().hex[:8]}"
        await lk.room.create_room(lk_api.CreateRoomRequest(name=room_name))
        dispatch = await lk.agent_dispatch.create_dispatch(
            lk_api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=json.dumps(meta),
            )
        )
        print(f"Outbound call dispatched → room={room_name} dispatch={dispatch.id}")
        print("Your phone will ring in ~5s.")
    finally:
        await lk.aclose()


async def cmd_seed_consent(user: str, phone: str) -> None:
    print(khata_memory.set_call_consent(user, True, phone=phone))
    profile = khata_memory.get_profile(user)
    print(f"Consent on record for user={user} phone={profile.get('phone')}")


async def cmd_call(user: str, deadline: str, reason: str) -> None:
    profile = khata_memory.get_profile(user)
    if not profile or not khata_memory.has_call_consent(user):
        print(
            f"BLOCKED: no consent + phone on record for user '{user}'. "
            f"Run: python src/run_outbound.py seed-consent --user {user} --phone +91... "
            "(or the shopkeeper must consent in-session)."
        )
        return
    print(khata_memory.schedule_reminder(user, deadline, reason))
    reminder = khata_memory.latest_reminder(user)
    if not reminder or reminder["status"] != "pending":
        return
    print(
        f"Reminder #{reminder['id']}: {reminder['reason']} due {reminder['deadline']} "
        f"→ calling {reminder['phone']}"
    )
    await _dispatch(
        {
            "phone_number": reminder["phone"],
            "user_id": user,
            "deadline": reminder["deadline"],
            "reason": reminder["reason"],
            "reminder_id": reminder["id"],
        }
    )


async def cmd_due(within: int) -> None:
    rows = khata_memory.due_reminders(within_days=within)
    if not rows:
        print("No pending reminders due.")
        return
    for r in rows:
        print(
            f"  #{r['id']} user={r['user_id']} {r['phone']} due {r['deadline']} ({r['reason']})"
        )
    print(
        f"\n{len(rows)} pending. Call one with: python src/run_outbound.py call --user <id>"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trigger outbound Khata-Vaani reminder calls"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "seed-consent",
        help="record consent + phone for a user (simulates in-session consent)",
    )
    p.add_argument("--user", required=True)
    p.add_argument("--phone", required=True, help="E.164 number, e.g. +919876543210")

    p = sub.add_parser("call", help="schedule + dispatch an outbound reminder call")
    p.add_argument("--user", required=True)
    p.add_argument("--deadline", default="2026-08-31")
    p.add_argument("--reason", default="PM SVANidhi application deadline")

    p = sub.add_parser("due", help="list pending reminders due soon")
    p.add_argument("--within", type=int, default=7)

    args = parser.parse_args()
    if args.command == "seed-consent":
        await cmd_seed_consent(args.user, args.phone)
    elif args.command == "call":
        await cmd_call(args.user, args.deadline, args.reason)
    elif args.command == "due":
        await cmd_due(args.within)


if __name__ == "__main__":
    asyncio.run(main())
