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
    cli,
    room_io,
    tokenize,
)
from livekit.plugins import deepgram, google, murf, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

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
1. Log a sale or udhaar entry: capture item/amount, and customer name if it's
   credit. Always read it back and confirm before treating it as saved.
2. Answer "who owes me how much" queries from what's been logged.
3. If asked for a summary, you may mention outstanding udhaar, but only when
   asked - don't volunteer it unprompted.

KNOWLEDGE
You only know what the shopkeeper has told you in this session (no real
persistence yet). You do not know market prices, loan terms, interest rates,
or anything about the customer beyond what's logged. Say so plainly when
asked something outside that.

LANGUAGE
Mirror the user. If they mix Hindi and English mid-sentence, understand it
and you may reply in the same mixed register if that's what they used first.
Keep sentences short - this is spoken, not read.

GUARDRAILS
- Never ask for OTP, PIN, UPI ID, or account/card numbers, under any framing.
- Never confirm a loan, credit line, or scheme approval - you only log what
  you're told, you don't verify or approve anything financial.
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

    # To add tools, use the @function_tool decorator.
    # Here's an example that adds a simple weather tool.
    # You also have to add `from livekit.agents import function_tool, RunContext` to the top of this file
    # @function_tool
    # async def lookup_weather(self, context: RunContext, location: str):
    #     """Use this tool to look up current weather information in the given location.
    #
    #     If the location is not supported by the weather service, the tool will indicate this. You must tell the user the location's weather is unavailable.
    #
    #     Args:
    #         location: The location to look up weather information for (e.g. city name)
    #     """
    #
    #     logger.info(f"Looking up weather for {location}")
    #
    #     return "sunny with a temperature of 70 degrees."


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
        # hi-Latn = Hinglish (Hindi in Latin script) — supports Hindi-English code-mixing, e.g. "hair oil, 150 rupee"
        stt=deepgram.STT(model="nova-3", language="hi-Latn"),
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


if __name__ == "__main__":
    cli.run_app(server)
