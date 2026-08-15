import asyncio
import json
import logging
import os
import random
import re
import time
import urllib.request
import uuid

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    room_io,
    tokenize,
)
from livekit.agents.llm import ChatContext
from livekit.plugins import deepgram, google, murf, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

import khata_memory
import outcome_log
import scheme

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# Khata-Vaani — voice bookkeeping agent for Indian shopkeepers.
silent_nudge = "Still there? Go ahead whenever you're ready."
silent_close = "It's been quiet, so I'll close up now. Take care, bye!"

# Outbound call tuning
VOICEMAIL_WAIT_S = 8.0  # no user speech this long after STT active → voicemail
HANGUP_WINDOW_S = (
    6.0  # hangup within this window of answering → immediate_hangup (opt-out)
)
RINGING_TIMEOUT_S = 30.0
RETRY_DELAY_S = 10.0  # pause before the one retry after no-answer/busy
_SIP_IDENTITY = "phone-user"

SYSTEM_PROMPT = """IDENTITY
You are Khata-Vaani, a voice assistant that helps small shopkeepers in India
track daily sales and udhaar (credit given to customers). You work for the
shopkeeper, not for any bank, lender, or platform.

OBJECTIVES
1. At the start of every session, call lookup_caller to check whether this
   shopkeeper is returning.
2. If returning, greet them by name and mention their most notable open
   udhaar - the customer with the highest balance, or the most recently
   updated one. If they are new, introduce yourself and ask their name and
   shop name, passing them to lookup_caller so they are remembered.
3. Log a sale or udhaar entry: capture item/amount, and customer name if it's
   credit. Before calling save_customer_entry, say out loud exactly what you
   are about to save and get explicit confirmation. If the shopkeeper says no
   or corrects you, do not call the save function.
4. Answer "who owes me how much" queries from the saved ledger.
5. If asked for a summary, you may mention outstanding udhaar, but only when
   asked - don't volunteer it unprompted.
6. Escalate to the team (create_escalation) in exactly two situations:
   - the shopkeeper reports something that sounds like fraud (fake currency,
     suspicious payment/OTP requests, someone impersonating a bank or scheme
     official)
   - the shopkeeper needs a judgment call you aren't allowed to make (e.g.
     whether to write off a debt or extend more credit to a customer)
   Before calling create_escalation, say out loud exactly what you're about to
   send and ask permission. If the shopkeeper says no, do not call the function.
7. After a successful escalation, read back the reference_id and give an
   honest next step - say "a team member will follow up", never a specific
   time like "someone will call in five minutes".

KNOWLEDGE
You only know what has been saved in this shopkeeper's khata (SQLite-backed
memory). You do not know market prices, loan terms, interest rates, or
anything about the customer beyond what's saved. Say so plainly when asked
something outside that.

LANGUAGE
Mirror the user. If they mix Hindi and English mid-sentence, understand it
and you may reply in the same mixed register if that's what they used first.
Keep sentences short - this is spoken, not read.

LANGUAGE & SCRIPT
Always write every language in its own native script. Hindi → Devanagari
(नमस्ते), never romanized (never 'namaste'). Same rule for all non-English
languages.

GUARDRAILS
- Never ask for OTP, PIN, UPI ID, or account/card numbers, under any framing.
- You may check published eligibility criteria for a government scheme and
  report a document checklist. You must never say a scheme, loan, or
  credit line is "approved" - only that criteria are met or not met, and
  always add that final approval happens through the scheme's official
  channel, not through you.
- Whenever you give any loan-scheme eligibility result, always say out loud
  that it is based on published guidelines as of the dataset's date, that it
  is not a live government check, and that final approval happens only
  through the official channel.
- Never store account numbers, card numbers, or any government ID numbers
  (Aadhaar, PAN, etc.) in any saved record, even if the shopkeeper offers
  them.
- Never advise whether the shopkeeper should extend more credit to a
  customer - that's their call.
- Never place an outbound call to a shopkeeper unless they explicitly agreed
  to be called back during a prior session (recorded via set_call_consent). If
  no such consent is on record, do not call - surface the reminder only the
  next time they call in.
- If asked anything outside logging sales/udhaar or recalling what's logged
  (loan advice, investments, payments), say: "That's outside what I handle -
  I just help you keep track of your khata." Then stop, don't improvise.

STYLE
Short sentences, under ~20 words. No lists, no brackets, nothing written for
a screen - this gets spoken aloud. If the user goes silent, the silence
handler covers re-prompting - don't add your own "are you there" logic here."""


OUTBOUND_PROMPT = """IDENTITY
You are Khata-Vaani, calling a shopkeeper to remind them about a scheme or
loan application deadline. You work for the shopkeeper, not any bank or lender.

TASK
1. After the opening line, listen. If they sound confused, repeat that this is
   Khata-Vaani calling about their loan application deadline.
2. If they say "stop", "don't call again", "mat karo", or similar, end the
   call immediately and call set_call_consent with consent=false.
3. Never discuss loan amounts, bank details, or payment links over the phone.
4. Keep it short. One reminder, then wrap up politely and say goodbye.

LANGUAGE
Mirror the user. Keep sentences short - this is spoken, not read.

GUARDRAILS
- Never ask for OTP, PIN, UPI ID, or account/card numbers.
- If they want to discuss eligibility or documents, tell them to call back in
  and say "scheme" - Khata-Vaani can help in their next session."""


def outbound_opening_line(deadline: str) -> str:
    """Two-sentence opening line: who we are, why we called, and how to opt out."""
    return (
        f"Namaste, this is Khata-Vaani calling to remind you about your loan "
        f"application deadline on {deadline}. Say stop and I won't call again."
    )


def _job_metadata(ctx: JobContext) -> dict:
    """Parse the dispatch metadata dict set by run_outbound.py."""
    meta = getattr(ctx.job, "metadata", None)
    if not meta:
        return {}
    try:
        return json.loads(meta)
    except (json.JSONDecodeError, AttributeError, TypeError):
        return {}


def _is_stop_request(text: str) -> bool:
    text = text.lower()
    return any(
        w in text
        for w in (
            "stop",
            "don't call",
            "dont call",
            "call mat",
            "mat karo",
            "band karo",
            "no call",
        )
    )


_PII_PATTERNS = [
    re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),  # PAN
    re.compile(r"\b\d[\d\s-]{4,}\d\b"),  # 6+ digit runs (OTP/account/card/Aadhaar)
]


def _strip_pii(text: str) -> str:
    """Remove OTP, PIN, account, card, Aadhaar, PAN numbers from free text."""
    for pattern in _PII_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def _new_reference_id() -> str:
    return f"KV-{random.randint(0, 9999):04d}"


def _post_discord(summary: dict) -> bool:
    """POST the escalation to the Discord channel webhook. Returns success."""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        logger.error("DISCORD_WEBHOOK_URL not set - escalation not sent to Discord")
        return False
    payload = {
        "content": f"New escalation {summary['reference_id']}",
        "embeds": [
            {
                "title": f"{summary['urgency'].upper()} — {summary['who']}",
                "description": summary["what_happened"],
                "fields": [
                    {
                        "name": "Reference",
                        "value": summary["reference_id"],
                        "inline": True,
                    },
                    {"name": "Urgency", "value": summary["urgency"], "inline": True},
                    {"name": "Status", "value": summary["status"], "inline": True},
                    {"name": "Language", "value": summary["language"], "inline": True},
                    {"name": "Already checked", "value": summary["already_checked"]},
                    {
                        "name": "Preferred followup",
                        "value": summary["preferred_followup"],
                    },
                    {"name": "Created", "value": summary["created_at"]},
                ],
            }
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 204
    except Exception as exc:
        logger.error("Discord webhook failed: %s", exc)
        return False


class _CallRecorder:
    """Per-call facts gathered from real events, used to derive the outcome."""

    def __init__(self) -> None:
        self.success = False
        self.tool_error = False
        self.user_spoke = False
        self.declined = False
        self.escalation_created = False
        self.finalized = False

    def mark_saved(self) -> None:
        self.success = True

    def mark_looked_up(self) -> None:
        self.success = True

    def mark_tool_error(self) -> None:
        self.tool_error = True

    def mark_declined(self) -> None:
        self.declined = True

    def mark_escalation(self) -> None:
        self.escalation_created = True


def _is_who_owes_query(text: str) -> bool:
    text = text.lower()
    return any(
        w in text
        for w in (
            "who owes",
            "kitna bakaya",
            "kitna udhaar",
            "kitna baaki",
            "bakaya kitna",
            "kiska kitna",
            "how much do they owe",
            "outstanding balance",
            "bakaya",
        )
    )


def _is_decline(text: str) -> bool:
    text = text.lower().strip()
    if any(w in text for w in ("mat karo", "nahi chahiye", "no need", "abhi nahi")):
        return True
    first = re.sub(r"[^a-z]+$", "", text.split()[0]) if text else ""
    return first in ("no", "nahi", "nhi")


class Assistant(Agent):
    def __init__(
        self, instructions: str = SYSTEM_PROMPT, opening_line: str | None = None
    ) -> None:
        chat_ctx = None
        if opening_line:
            chat_ctx = ChatContext()
            chat_ctx.add_message(role="assistant", content=opening_line)
        super().__init__(instructions=instructions, chat_ctx=chat_ctx)

    @staticmethod
    def _user_id(context: RunContext) -> str:
        """Resolve the caller's id; falls back to 'unknown' when userdata is unset."""
        try:
            return (context.userdata or {}).get("user_id", "unknown")
        except Exception:
            return "unknown"

    @function_tool
    async def lookup_caller(
        self,
        context: RunContext,
        user_id: str = "",
        name: str | None = None,
        shop_name: str | None = None,
    ):
        """Look up the shopkeeper's saved profile and customer ledger.

        Call this once at the start of a session to check whether the shopkeeper
        is returning. Returns their name, shop name, language preference, usual
        items sold, and each customer's udhaar balance (highest first).

        Args:
            user_id: The shopkeeper's caller id. Leave empty to use the session
                caller automatically.
            name: The shopkeeper's name, if they just told you it. It will be
                remembered for next time.
            shop_name: The shop name, if they just told you it. It will be
                remembered for next time.
        """
        resolved_id = user_id or self._user_id(context)
        if name or shop_name:
            khata_memory.remember_profile(resolved_id, name=name, shop_name=shop_name)
        logger.info("lookup_caller user=%s", resolved_id)
        return khata_memory.lookup_caller(resolved_id)

    @function_tool
    async def save_customer_entry(
        self, context: RunContext, name: str, amount: float, entry_type: str
    ):
        """Save a sale or update a customer's udhaar balance.

        Only call this AFTER the shopkeeper has explicitly confirmed the details
        out loud. Use entry_type "udhaar" to add `amount` to a customer's credit
        balance, or "sale" to log an item sold.

        Args:
            name: Customer name for udhaar, or item name for a sale.
            amount: Amount in rupees.
            entry_type: "udhaar" or "sale".
        """
        user_id = self._user_id(context)
        logger.info(
            "save_customer_entry user=%s type=%s name=%s amount=%s",
            user_id,
            entry_type,
            name,
            amount,
        )
        result = khata_memory.save_customer_entry(user_id, name, amount, entry_type)
        recorder = getattr(self, "_recorder", None)
        if recorder:
            if "saved" in result or "logged" in result:
                recorder.mark_saved()
            elif result.startswith("Unknown entry_type"):
                recorder.mark_tool_error()
        return result

    @function_tool
    async def check_scheme_eligibility(
        self,
        context: RunContext,
        vending_certificate: bool | None = None,
        previous_loans: int | None = None,
    ):
        """Check whether the shopkeeper likely qualifies for the PM SVANidhi street-vendor loan scheme, and which loan tier applies.

        Use when the shopkeeper asks things like "can I get a loan", "am I eligible for any government scheme", "which loan can I get", or "what documents do I need". Uses the shopkeeper's saved khata profile (sales activity) where available instead of re-asking. Returns an eligibility assessment, the likely loan tier (first/second/third) with the amount, and the document checklist.

        Args:
            vending_certificate: Whether the shopkeeper has a vending certificate or letter of recommendation, if they have told you.
            previous_loans: How many PM SVANidhi loans they have already taken and repaid, if they have told you.
        """
        user_id = self._user_id(context)
        logger.info("check_scheme_eligibility user=%s", user_id)
        profile = khata_memory.get_profile(user_id)
        try:
            return scheme.check_eligibility(
                profile,
                vending_certificate=vending_certificate,
                previous_loans=previous_loans,
            )
        except Exception:
            recorder = getattr(self, "_recorder", None)
            if recorder:
                recorder.mark_tool_error()
            raise

    @function_tool
    async def set_call_consent(
        self, context: RunContext, consent: bool, phone: str | None = None
    ):
        """Record whether the shopkeeper explicitly agreed to receive outbound reminder calls.

        Only call when they explicitly agree to being called about a scheme or loan
        deadline. Their phone number is required to say yes. If they say stop, don't
        call again, or hang up immediately, call this with consent=false to revoke.

        Args:
            consent: True if they explicitly agreed to receive calls, False to revoke.
            phone: Their phone number in E.164 format (e.g. +919876543210), required to consent.
        """
        user_id = self._user_id(context)
        logger.info("set_call_consent user=%s consent=%s", user_id, consent)
        if consent and not phone:
            return "I need their phone number before I can set up call reminders."
        return khata_memory.set_call_consent(user_id, consent, phone=phone)

    @function_tool
    async def schedule_reminder_call(
        self, context: RunContext, deadline: str, reason: str
    ):
        """Queue an outbound reminder call before a scheme or loan application deadline.

        Call only after the shopkeeper explicitly agreed to be called and set_call_consent
        succeeded. If no consent is on record this refuses - surface the reminder in-session
        instead of scheduling a call.

        Args:
            deadline: Application deadline as an ISO date (YYYY-MM-DD).
            reason: Why they should be reminded, e.g. "PM SVANidhi application deadline".
        """
        user_id = self._user_id(context)
        logger.info("schedule_reminder_call user=%s deadline=%s", user_id, deadline)
        return khata_memory.schedule_reminder(user_id, deadline, reason)

    @function_tool
    async def create_escalation(
        self,
        context: RunContext,
        what_happened: str,
        already_checked: str,
        urgency: str,
        preferred_followup: str,
    ):
        """Escalate an issue to the Khata-Vaani team.

        Call ONLY after telling the shopkeeper out loud what will be sent and
        getting their explicit permission. Use for suspected fraud (fake currency,
        suspicious payment/OTP requests, someone impersonating a bank or scheme
        official) or a judgment call you are not allowed to make (e.g. whether to
        write off a debt or extend more credit).

        Args:
            what_happened: 1-2 sentence summary of the issue.
            already_checked: What you already tried or advised.
            urgency: "low", "medium", "high", or "emergency".
            preferred_followup: How the shopkeeper wants the team to reach them.
        """
        user_id = self._user_id(context)
        profile = khata_memory.get_profile(user_id)
        who = (
            f"{profile.get('name', '') or ''} {profile.get('shop_name', '') or ''}".strip()
            or "Unknown shopkeeper"
        )
        summary = {
            "reference_id": _new_reference_id(),
            "who": who,
            "what_happened": _strip_pii(what_happened)[:500],
            "already_checked": _strip_pii(already_checked)[:500],
            "urgency": urgency,
            "language": profile.get("language_preference") or "unknown",
            "preferred_followup": _strip_pii(preferred_followup)[:200],
            "status": "open",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

        dup = khata_memory.find_similar_open_escalation(
            user_id, summary["what_happened"]
        )
        if dup:
            summary["reference_id"] = dup["reference_id"]
            khata_memory.save_escalation({**summary, "user_id": user_id})
            logger.info(
                "Updated escalation %s (dup) user=%s", summary["reference_id"], user_id
            )
            return (
                f"Updated existing escalation {summary['reference_id']}. "
                "A team member will follow up."
            )

        khata_memory.save_escalation({**summary, "user_id": user_id})
        recorder = getattr(self, "_recorder", None)
        if recorder:
            recorder.mark_escalation()
        posted = _post_discord(summary)
        logger.info(
            "Escalation %s created user=%s urgency=%s discord=%s",
            summary["reference_id"],
            user_id,
            urgency,
            posted,
        )
        if posted:
            return (
                f"Escalation {summary['reference_id']} recorded and sent to the team "
                f"({urgency} urgency). A team member will follow up."
            )
        return (
            f"Escalation {summary['reference_id']} recorded, but the team alert could "
            "not be delivered right now. It is on file - a team member will follow up."
        )


server = AgentServer()


async def _dial_with_retry(
    ctx: JobContext, meta: dict, phone_number: str
) -> tuple[bool, str]:
    """Place the SIP call; retry once after RETRY_DELAY_S on no-answer/busy."""
    from livekit import api as lk_api

    trunk_id = os.environ.get("LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "")
    if not trunk_id:
        logger.error("LIVEKIT_SIP_OUTBOUND_TRUNK_ID not set - cannot dial outbound")
        return False, "config_error"

    user_id = meta.get("user_id", "unknown")
    if not khata_memory.has_call_consent(user_id):
        logger.warning("No consent on record for %s - not dialing", user_id)
        return False, "no_consent"

    lk = lk_api.LiveKitAPI(
        url=os.environ["LIVEKIT_URL"],
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
    )
    last_reason = "no_answer"
    try:
        for attempt in (1, 2):
            logger.info(
                "Dialing %s (trunk %s), attempt %d...", phone_number, trunk_id, attempt
            )
            try:
                await lk.sip.create_sip_participant(
                    lk_api.CreateSIPParticipantRequest(
                        sip_trunk_id=trunk_id,
                        sip_call_to=phone_number,
                        room_name=ctx.room.name,
                        participant_identity=_SIP_IDENTITY,
                        wait_until_answered=True,
                        ringing_timeout=RINGING_TIMEOUT_S,
                    )
                )
                return True, "answered"
            except Exception as exc:
                last_reason = "busy" if "busy" in str(exc).lower() else "no_answer"
                logger.warning("Attempt %d failed (%s): %s", attempt, last_reason, exc)
                if attempt == 1:
                    await asyncio.sleep(RETRY_DELAY_S)
        return False, last_reason
    finally:
        await lk.aclose()


async def _greet_answered_call(
    ctx: JobContext, session: AgentSession, opening_line: str
) -> rtc.RemoteParticipant | None:
    """Wait for the answered SIP participant, then play the opening line before STT."""
    participant = ctx.room.remote_participants.get(_SIP_IDENTITY)
    if participant is None:
        try:
            participant = await asyncio.wait_for(
                ctx.wait_for_participant(), timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.error("SIP participant didn't appear after answering")
            return None

    track_ready = asyncio.Event()

    def _on_track_subscribed(track, _pub, remote_participant):
        if (
            remote_participant.identity == participant.identity
            and track.kind == rtc.TrackKind.KIND_AUDIO
        ):
            track_ready.set()

    ctx.room.on("track_subscribed", _on_track_subscribed)
    for pub in participant.track_publications.values():
        if pub.subscribed and pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
            track_ready.set()
            break
    try:
        await asyncio.wait_for(track_ready.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        logger.warning("Caller audio track not detected in 3s (proceeding)")
    finally:
        ctx.room.off("track_subscribed", _on_track_subscribed)

    await asyncio.sleep(0.7)

    handle = session.say(opening_line, allow_interruptions=False)
    await asyncio.wait_for(handle.wait_for_playout(), timeout=60.0)
    session.room_io.set_participant(participant.identity)
    return participant


async def _handle_outbound_outcomes(
    ctx: JobContext,
    session: AgentSession,
    meta: dict,
    participant: rtc.RemoteParticipant,
    answer_t: float,
) -> None:
    """Classify and log the call outcome; revoke consent on stop / immediate hangup."""
    user_id = meta.get("user_id", "unknown")
    reminder_id = meta.get("reminder_id")

    stop_seen = asyncio.Event()
    disconnected = asyncio.Event()
    first_speech = asyncio.Event()

    def _on_user_input(ev):
        if ev.is_final and ev.transcript:
            first_speech.set()
            if _is_stop_request(ev.transcript):
                stop_seen.set()

    def _on_disconnected(*_):
        disconnected.set()

    session.on("user_input_transcribed", _on_user_input)
    ctx.room.on("disconnected", _on_disconnected)

    def _finish(outcome: str) -> None:
        logger.info("OUTBOUND outcome: %s", outcome)
        outcome_log.log_call(meta, outcome)
        if reminder_id:
            status = {"opted_out": "opted_out", "voicemail": "voicemail"}.get(
                outcome, "called"
            )
            khata_memory.mark_reminder(reminder_id, status)
        if outcome in ("opted_out", "immediate_hangup"):
            khata_memory.set_call_consent(user_id, False)
        if outcome in ("opted_out", "immediate_hangup", "voicemail"):
            session.shutdown()

    # Wait for first user speech OR hangup, up to the voicemail window.
    # ponytail: no AMD on the LiveKit trunk path - "no speech for VOICEMAIL_WAIT_S"
    # is the voicemail heuristic; add AMD later if misclassification shows up.
    await asyncio.wait(
        {first_speech.wait(), disconnected.wait()},
        timeout=VOICEMAIL_WAIT_S,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if disconnected.is_set():
        if time.monotonic() - answer_t < HANGUP_WINDOW_S:
            _finish("immediate_hangup")
        else:
            _finish("completed")
        return
    if stop_seen.is_set():
        _finish("opted_out")
        return
    if not first_speech.is_set():
        _finish("voicemail")
        return

    # They spoke - keep listening for "stop" or hangup until the call ends.
    while not stop_seen.is_set() and not disconnected.is_set():
        await asyncio.wait(
            {stop_seen.wait(), disconnected.wait()},
            return_when=asyncio.FIRST_COMPLETED,
        )
    if stop_seen.is_set():
        _finish("opted_out")
    else:
        _finish("completed")


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext):
    # Logging setup
    # Add any other context you want in all log entries here
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    job_meta = _job_metadata(ctx)
    outbound_phone = job_meta.get("phone_number")
    opening_line = (
        outbound_opening_line(job_meta.get("deadline", "soon"))
        if outbound_phone
        else None
    )
    instructions = OUTBOUND_PROMPT if outbound_phone else SYSTEM_PROMPT
    recorder = _CallRecorder()
    call_id = f"call-{uuid.uuid4().hex[:8]}"

    # Set up a voice AI pipeline using Murf Falcon, Gemini, Deepgram, and the LiveKit turn detector
    session = AgentSession(
        # Speech-to-text (STT) is your agent's ears, turning the user's speech into text that the LLM can understand
        # See all available models at https://docs.livekit.io/agents/models/stt/
        # "multi" = Deepgram auto-detects language per utterance, so code-switched
        # Hindi/English ("hair oil, 150 rupee") transcribes correctly
        stt=deepgram.STT(model="nova-3", language="multi"),
        # A Large Language Model (LLM) is your agent's brain, processing user input and generating a response
        # See all available models at https://docs.livekit.io/agents/models/llm/
        llm=google.LLM(
            model="gemini-3.5-flash-lite",
        ),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        tts=murf.TTS(
            voice="Pooja",  # en-IN Indian English (Falcon 2) — Murf voice library
            locale="en-IN",
            style="Conversation",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
            text_pacing=True,
        ),
        # VAD and turn detection are used to determine when the user is speaking and when the agent should respond
        # See more at https://docs.livekit.io/agents/build/turns
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        # allow the LLM to generate a response while waiting for the end of turn
        # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
        preemptive_generation=True,
        # mark the user as "away" after this much silence (both agent and user
        # quiet) so we can nudge them — no fixed sleep timers needed
        user_away_timeout=6.0,
    )

    # Measure end-of-speech -> first audio out latency.
    # Start clock once the user's utterance finishes transcribing (end of their speech),
    # stop at the first agent audio playback (agent state flips to "speaking").
    speech_end_ts = {"t": None}

    @session.on("user_input_transcribed")
    def _on_user_input(ev):
        if ev.is_final:
            speech_end_ts["t"] = time.perf_counter()

    @session.on("agent_state_changed")
    def _on_agent_state(ev):
        if ev.new_state == "speaking" and speech_end_ts["t"] is not None:
            ms = (time.perf_counter() - speech_end_ts["t"]) * 1000
            logger.info(f"[LATENCY] end-of-speech to first audio out: {ms:.1f}ms")
            speech_end_ts["t"] = None

    # Silence handling — driven by LiveKit's built-in "away" detection
    # (user_away_timeout). First silent gap: gentle nudge. Still silent after
    # the nudge: close politely instead of repeating forever.
    away_state = {"nudges": 0, "watch": None}

    async def _close_after_silence() -> None:
        try:
            await asyncio.sleep(6.0)
            if session.user_state == "away":
                handle = session.say(silent_close, add_to_chat_ctx=False)
                handle.add_done_callback(lambda _: session.shutdown())
        except asyncio.CancelledError:
            pass

    @session.on("user_state_changed")
    def _on_user_state(ev):
        if ev.new_state == "away":
            away_state["nudges"] += 1
            if away_state["nudges"] == 1:
                session.say(silent_nudge, add_to_chat_ctx=False)
        else:
            # user spoke (or is speaking) — this is a fresh gap
            away_state["nudges"] = 0
            if away_state["watch"] is not None:
                away_state["watch"].cancel()
                away_state["watch"] = None

    @session.on("agent_state_changed")
    def _on_agent_state_silence(ev):
        if (
            ev.new_state == "listening"
            and away_state["nudges"] == 1
            and away_state["watch"] is None
        ):
            away_state["watch"] = asyncio.create_task(_close_after_silence())

    # To use a realtime model instead of a voice pipeline, use the following session setup instead.
    # (Note: This is for the OpenAI Realtime API. For other providers, see https://docs.livekit.io/agents/models/realtime/))
    # 1. Install livekit-agents[openai]
    # 2. Set OPENAI_API_KEY in .env.local
    # 3. Add `from livekit.plugins import openai` to the top of this file
    # 4. Use the following session setup instead of the version above
    # session = AgentSession(
    #     llm=openai.realtime.RealtimeModel(voice="marin")
    # )

    # # Add a virtual avatar to the session, if desired
    # # For other providers, see https://docs.livekit.io/agents/models/avatar/
    # avatar = hedra.AvatarSession(
    #   avatar_id="...",  # See https://docs.livekit.io/agents/models/avatar/plugins/hedra
    # )
    # # Start the avatar and wait for it to join
    # await avatar.start(session, room=ctx.room)

    # Start the session, which initializes the voice pipeline and warms up the models
    assistant = Assistant(instructions=instructions, opening_line=opening_line)
    assistant._recorder = recorder
    await session.start(
        agent=assistant,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind
                    == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
                ),
            ),
        ),
    )

    # Join the room and connect to the user
    await ctx.connect()

    # Persist the caller's identity so the memory tools can key the ledger on it
    if outbound_phone:
        session.userdata = {"user_id": job_meta.get("user_id", "unknown")}
    else:
        remote = (
            next(iter(ctx.room.remote_participants))
            if ctx.room.remote_participants
            else "unknown"
        )
        session.userdata = {"user_id": remote}

    # Call analytics: open a row, observe real events, close it on disconnect.
    is_sip = outbound_phone or any(
        p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        for p in ctx.room.remote_participants.values()
    )
    user_id = session.userdata["user_id"]
    language = khata_memory.get_profile(user_id).get("language_preference") or "unknown"
    khata_memory.start_call(call_id, "sip" if is_sip else "browser", language)

    @session.on("user_input_transcribed")
    def _on_user_input_call(ev):
        if ev.is_final and ev.transcript:
            recorder.user_spoke = True
            if _is_who_owes_query(ev.transcript):
                recorder.mark_looked_up()
            if _is_decline(ev.transcript):
                recorder.mark_declined()

    def _finalize_call(*_args):
        if recorder.finalized:
            return
        recorder.finalized = True
        failure_type = None
        if recorder.success:
            khata_memory.end_call(call_id, "success", None)
        else:
            if recorder.tool_error:
                failure_type = "tool_error"
            elif not recorder.user_spoke:
                failure_type = "no_response"
            elif recorder.declined:
                failure_type = "user_declined"
            else:
                failure_type = "incomplete_hangup"
            khata_memory.end_call(call_id, "failed", failure_type)
        if recorder.escalation_created:
            khata_memory.set_call_escalation(call_id)
        logger.info(
            "CALL %s ended: outcome=%s failure_type=%s",
            call_id,
            "success" if recorder.success else "failed",
            failure_type,
        )

    ctx.room.on("disconnected", _finalize_call)

    # Outbound: dial, greet, then observe outcomes (voicemail / opt-out / hangup).
    if outbound_phone:
        ok, dial_outcome = await _dial_with_retry(ctx, job_meta, outbound_phone)
        if not ok:
            logger.warning("Dial failed: %s", dial_outcome)
            outcome_log.log_call(job_meta, dial_outcome)
            if job_meta.get("reminder_id"):
                khata_memory.mark_reminder(job_meta["reminder_id"], "failed")
            session.shutdown()
            return
        participant = await _greet_answered_call(ctx, session, opening_line)
        if participant is None:
            outcome_log.log_call(job_meta, "no_participant")
            session.shutdown()
            return
        await _handle_outbound_outcomes(
            ctx, session, job_meta, participant, time.monotonic()
        )


if __name__ == "__main__":
    cli.run_app(server)
