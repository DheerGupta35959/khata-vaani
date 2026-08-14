# Backend — Voice Agent with Murf Falcon TTS

The Python backend for the Voice Agent Starter. It runs a real-time voice AI pipeline using [LiveKit Agents](https://docs.livekit.io/agents), connecting Murf Falcon TTS, Deepgram STT, and Google Gemini into a single conversational agent.

## How It Works

```
User speaks → [Deepgram STT] → text → [Gemini LLM] → response → [Murf Falcon TTS] → audio → User hears
```

LiveKit handles the real-time audio transport. The agent connects to LiveKit as a participant, listens for user speech, and responds with synthesized audio.

## Setup

### 1. Install dependencies

```bash
cd backend
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env.local
```

Fill in your keys in `.env.local`:

| Variable             | Where to get it                                           |
| -------------------- | --------------------------------------------------------- |
| `LIVEKIT_URL`        | [LiveKit Cloud](https://cloud.livekit.io/) → Settings     |
| `LIVEKIT_API_KEY`    | [LiveKit Cloud](https://cloud.livekit.io/) → Settings     |
| `LIVEKIT_API_SECRET` | [LiveKit Cloud](https://cloud.livekit.io/) → Settings     |
| `MURF_API_KEY`       | [murf.ai/api/dashboard](https://murf.ai/api/dashboard)    |
| `DEEPGRAM_API_KEY`   | [deepgram.com](https://console.deepgram.com/)             |
| `GOOGLE_API_KEY`     | [aistudio.google.com](https://aistudio.google.com/apikey) |

For LiveKit Cloud users, you can auto-populate LiveKit credentials:

```bash
lk cloud auth
lk app env -w -d .env.local
```

### 3. Download models

```bash
uv run python src/agent.py download-files
```

This downloads Silero VAD and the LiveKit turn detector models.

### 4. Run the agent

```bash
# Development mode (auto-reload)
uv run python src/agent.py dev

# Or test directly in your terminal (no frontend needed)
uv run python src/agent.py console

# Production
uv run python src/agent.py start
```

## Configuration

All configuration lives in [`src/agent.py`](src/agent.py).

### System prompt

The `SYSTEM_PROMPT` constant at the top of `agent.py` controls what your agent does. Change it to build any voice-powered use case.

#### Example prompts

**Customer Support (default):**

```
You are a friendly and efficient customer support agent for a tech company. Help users with account issues, billing questions, and product troubleshooting. Be concise, empathetic, and solution-oriented. If you don't know something, say so honestly and offer to escalate.
```

**Language Tutor:**

```
You are a patient and encouraging language tutor helping the user practice conversational Spanish. Speak primarily in Spanish but switch to English to explain grammar or vocabulary when needed. Correct mistakes gently and suggest better phrasing. Keep conversations natural and fun.
```

**AI Receptionist:**

```
You are a professional receptionist for a medical clinic. Help callers schedule appointments, answer questions about office hours and services, and take messages for doctors. Be warm but efficient. Ask for the caller's name and reason for calling upfront.
```

**Interview Coach:**

```
You are an experienced interview coach. Conduct mock interviews with the user for software engineering roles. Ask one behavioral or technical question at a time, let the user answer fully, then give specific feedback on their response — what was strong, what could improve, and a suggested reframe. Keep the tone encouraging but honest.
```

**Sales Assistant:**

```
You are a knowledgeable sales assistant for an electronics store. Help customers find the right product by asking about their needs, budget, and preferences. Compare options clearly, highlight trade-offs, and make a recommendation. Never be pushy — focus on helping the customer make the best decision for them.
```

**Fitness Coach:**

```
You are an upbeat personal fitness coach. Help users plan workouts, suggest exercises for specific muscle groups, and answer questions about form and technique. Ask about their fitness level and any injuries before recommending exercises. Keep instructions clear and motivating.
```

**Storyteller / Bedtime Narrator:**

```
You are a creative storyteller who tells original bedtime stories for children aged 4–8. Ask the child (or parent) for a character name, a favorite animal, and a setting, then weave a short, calming story. Use vivid but simple language. End each story on a peaceful, sleepy note.
```

**Meeting Summarizer:**

```
You are a meeting assistant. The user will describe what happened in a meeting or read you their notes. Summarize the key decisions, action items (with owners if mentioned), and any open questions. Be concise and structured. Ask clarifying questions if something is ambiguous.
```

**Trivia Game Host:**

```
You are an enthusiastic trivia game host. Ask the user one trivia question at a time from a mix of categories — science, history, pop culture, geography, and sports. Wait for their answer, tell them if they're right or wrong, give a brief fun fact, then move to the next question. Keep score and announce it every 5 questions.
```

**Mental Health Check-in Companion:**

```
You are a gentle, non-clinical wellness companion. Help users talk through their day, reflect on how they're feeling, and practice simple grounding exercises like deep breathing or gratitude lists. You are not a therapist — if the user expresses serious distress or mentions self-harm, gently encourage them to reach out to a professional or crisis helpline.
```

### Voice

Set the `voice` argument in the `murf.TTS(...)` call:

```python
tts=murf.TTS(
    voice="en-US-matthew",    # Change this
    style="Conversation",
    tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
    text_pacing=True
)
```

Some voice options:

| Voice ID | Description                      |
| -------- | -------------------------------- |
| `Anisha` | Indian English, female (default) |
| `Pooja`  | Indian English, female           |
| `Samar`  | Indian English, male             |
| `Amara`  | US English, female               |
| `Hazel`  | UK English, female               |
| `Bertie` | UK English, male                 |
| `Gordon` | US English, male                 |

Browse all 150+ voices: [Murf Voice Library](https://murf.ai/api/docs/voices-styles/voice-library).

### STT (Speech-to-Text)

Default is Deepgram Nova-3. Change in the `AgentSession(stt=...)` call:

```python
stt=deepgram.STT(model="nova-3")
```

### LLM

Default is Google Gemini. To switch:

- **Gemini (default):** Set `GOOGLE_API_KEY` in `.env.local`
- **OpenAI:** Set `OPENAI_API_KEY`, install `livekit-agents[openai]`, and change the `llm=` argument

### PM SVANidhi eligibility dataset

The `check_scheme_eligibility` tool reads a **hand-built local dataset** at
[`src/scheme_data.json`](src/scheme_data.json) — it is **not a live government
API**. It summarizes the published PM SVANidhi guidelines:

- Loan tiers (first ₹15,000 / second ₹25,000 / third ₹50,000) and scheme
  features were confirmed against the official portal
  [pmsvanidhi.mohua.gov.in](https://pmsvanidhi.mohua.gov.in/) (last updated
  07-08-2026).
- Vendor-type requirements and the document checklist follow the PM SVANidhi
  operational guidelines issued by the Ministry of Housing and Urban Affairs
  (MoHUA).

Because this is a hand-curated snapshot, amounts and rules may drift from the
live scheme. The agent is instructed to always state the dataset date and that
final approval happens through the official channel, and to refuse an
assessment if the dataset fails to load.

To update the criteria, edit `src/scheme_data.json` and re-run
`uv run python src/scheme.py` (self-check).

## Outbound calls (reminders)

The agent can place outbound reminder calls before a scheme/loan application
deadline — **only** when the shopkeeper explicitly consented to be called
during a session (`set_call_consent`) and left a phone number. Without consent
on record the agent refuses to dial and the reminder surfaces the next time
the shopkeeper calls in.

### 1. Twilio setup (console.twilio.com, once)

1. Elastic SIP Trunking → Trunks → Create new SIP Trunk (name: "LiveKit Outbound").
2. Open the trunk → Termination tab → Enable termination. Pick a unique SIP URI
   subdomain, e.g. `livekit-outbound.pstn.twilio.com`. Save.
3. Termination → Credential Lists → create a credential list (`livekit-creds`)
   with a username + password, then attach it to the trunk's Termination tab.

Add to `.env.local`:
```
TWILIO_SIP_TERM_URI=livekit-outbound.pstn.twilio.com
TWILIO_SIP_USERNAME=<credential username>
TWILIO_SIP_PASSWORD=<credential password>
TWILIO_PHONE_NUMBER=+12015551234
```

### 2. Create the LiveKit outbound trunk (once)

```bash
uv run python scripts/setup_outbound_trunk.py
```
Copy the printed trunk ID into `.env.local`:
```
LIVEKIT_SIP_OUTBOUND_TRUNK_ID=KT_xxxxxxxxxxxxxxxx
```

### 3. Trigger a test call to your own number

Start the agent worker (it must be running for a dispatch to be picked up):

```bash
uv run python src/agent.py dev
```

In a second terminal, record consent for a test user and dispatch a call:

```bash
# Simulates the in-session consent + phone the shopkeeper would give out loud
uv run python src/run_outbound.py seed-consent --user test --phone +919876543210

# Schedule + dial a reminder for that deadline
uv run python src/run_outbound.py call --user test --deadline 2026-08-31 --reason "PM SVANidhi application deadline"
```

Your phone rings in ~5s. The opening line identifies Khata-Vaani and the
deadline, and says "Say stop and I won't call again." Outcomes (answered,
voicemail, opted_out, immediate_hangup, no_answer, busy) are appended to
`logs/outbound_calls.jsonl`; reminders are tracked in `khata.db`. "stop" or an
immediate hang-up revokes consent, so no further calls are placed.

List pending reminders due within the week:

```bash
uv run python src/run_outbound.py due
```

## Testing

The project includes an eval suite based on the LiveKit Agents [testing framework](https://docs.livekit.io/agents/build/testing/):

```bash
uv run pytest
```

Tests are in [`tests/test_agent.py`](tests/test_agent.py) and use LLM-as-judge evaluations to verify the agent behaves correctly (friendly greetings, grounding, refusing harmful requests).

To run tests in CI, you'll need to add `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` as repository secrets.

## Deployment

### Railway

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/tIVCF1?referralCode=cNjn2P&utm_medium=integration&utm_source=template&utm_campaign=generic)

Set these environment variables in Railway:

- `MURF_API_KEY`
- `DEEPGRAM_API_KEY`
- `GOOGLE_API_KEY`
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`

### Docker

A production-ready [Dockerfile](Dockerfile) is included:

```bash
docker build -t murf-voice-agent .
docker run --env-file .env.local murf-voice-agent
```

## Project Structure

```
backend/
├── src/
│   └── agent.py          # Agent entrypoint — pipeline, prompt, config
├── tests/
│   └── test_agent.py     # LLM-judged eval suite
├── .env.example           # Environment variable template
├── pyproject.toml         # Python dependencies (uv)
├── Dockerfile             # Production container
└── railway.toml           # Railway deploy config
```

## Links

- [Murf Falcon TTS Docs](https://murf.ai/api/docs/text-to-speech/streaming)
- [Murf Voice Library](https://murf.ai/api/docs/voices-styles/voice-library)
- [LiveKit Agents Docs](https://docs.livekit.io/agents)
- [Deepgram Nova-3 Docs](https://developers.deepgram.com)

## License

MIT — see [LICENSE](LICENSE).
