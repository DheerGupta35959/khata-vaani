import asyncio
import logging
import time

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
from livekit.plugins import deepgram, google, murf, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

import khata_memory
import scheme

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# Khata-Vaani — voice bookkeeping agent for Indian shopkeepers.
silent_nudge = "Still there? Go ahead whenever you're ready."
silent_close = "It's been quiet, so I'll close up now. Take care, bye!"

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
- If asked anything outside logging sales/udhaar or recalling what's logged
  (loan advice, investments, payments), say: "That's outside what I handle -
  I just help you keep track of your khata." Then stop, don't improvise.

STYLE
Short sentences, under ~20 words. No lists, no brackets, nothing written for
a screen - this gets spoken aloud. If the user goes silent, the silence
handler covers re-prompting - don't add your own "are you there" logic here."""


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)

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
            khata_memory.remember_profile(
                resolved_id, name=name, shop_name=shop_name
            )
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
        return khata_memory.save_customer_entry(user_id, name, amount, entry_type)

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
        return scheme.check_eligibility(
            profile,
            vending_certificate=vending_certificate,
            previous_loans=previous_loans,
        )


server = AgentServer()


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
                text_pacing=True
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
    await session.start(
        agent=Assistant(),
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
    remote = next(iter(ctx.room.remote_participants)) if ctx.room.remote_participants else "unknown"
    session.userdata = {"user_id": remote}


if __name__ == "__main__":
    cli.run_app(server)
