# Voice assistant: lessons from the VoiceAgent phone project

Status: **proposal, for review.** Nothing here is built yet. Written 2026-11-24; the owner's
answers to the open questions were added the same day (see "Decisions from the owner" near the
end).

This plan takes what was learned building a separate phone receptionist
(`/home/dak/Desktop/VoiceAgent`, LiveKit Agents 1.8.10, self-hosted, answering real calls
over SIP) and applies it to Mu3Lab's browser voice assistant (`apps/livekit`). That project
is phone-only and Mu3Lab is web-only, so the SIP, transfer and dental-booking parts do not
carry over. The work that does carry over makes turns faster, makes the voice sound more
natural, and makes changes the assistant makes safe.

Read this whole file before starting. Each item says **why** (with evidence), **where**
in Mu3Lab, **what to do**, and **done when**.

## Ground rules for the implementing agent

- Look up every LiveKit API in the docs (`lk docs …` or the LiveKit docs MCP server) before
  writing LiveKit code. Names marked *check in docs* below are from memory or from 1.8.10, and
  Mu3Lab pins 1.8.8.
- Keep Mu3Lab's conventions: settings flow `ctl/voice_settings.py` → `voice-tools/settings.json`
  → the agent reads it at each call's start (no restart). Plain-English UI text. `make verify`
  must pass.
- Measure before and after. Mu3Lab already logs one `Reply after …` line per turn
  (`log_turns`). Use it for every speed item, and record the numbers in this file.
- Small, reviewable steps; one item per commit. Ask the owner before changing defaults that
  everyone hears (voice, turn timing), and before adding a new container or model download.
- The repo has unrelated uncommitted work (`git status`); touch only the files each item
  names.

## How the two compare today

| Stage | VoiceAgent (phone, 2 vCPU, no GPU) | Mu3Lab (browser, RTX 3060) |
| --- | --- | --- |
| Speech to text | **Streaming** sherpa-onnx server; partial text while the person talks; VAD stop sends a *flush* and the final arrives in **~33 ms** [measured] | Whisper turbo through Speaches, **one REST request per utterance** (`use_realtime=False`), so recognition starts only after speech ends (~0.4 s warm) |
| End of turn | Silero VAD 0.5 s + turn detector v1-mini, endpointing 0.5 / 2.0 s, the detector's score logged on every turn | VAD 0.25 s + v1-mini, 0.25 / 1–3 s ("responsiveness") |
| Answer model | `FallbackAdapter` chain in the agent; a provider that sends nothing for 2 s is skipped; 1 retry of the whole chain; reasoning off and excluded | LiteLLM routes (local Qwen → FreeLLMAPI list → any outside model); 8 s / 4 s hand-over; agent waits 30 s for first words; **LiveKit's own retries left at the default** |
| Text to speech | Pocket TTS in-process, **streams inside a sentence**: first audio 0.10–0.13 s on the server [measured] | Kokoro through Speaches REST, one request per sentence; full model on GPU ~0.15 s per sentence |
| Text clean-up for the voice | typographic characters → ASCII; glued duplicate answers cut; dates and times given to the model ready to say | markdown and emoji filtered by LiveKit's defaults only |
| Slow answer | "One moment, please." if no first word after 1.5 s | fillers only while a **tool** runs; nothing while the model itself is slow |
| Confirming a change | code checks a **later** user turn replied to the **latest** proposal, whose read-back played in full; the model judges the meaning | regex `YES` on the start of the latest user message |
| Silence | re-asks after 10 s (twice), then ends the call | none |
| Tests | 68 (state guards, silence check, settings, LLM chain, conversation tests) | none for the agent |

## Phase 1: quick, low-risk wins (one sitting)

### 1.1 Stop LiveKit's own retries from stacking on LiteLLM's

- **Why:** in VoiceAgent, a provider error made LiveKit retry the whole LLM request 3 more
  times, 2 s apart: **~8 s of silence** on a real call [measured 2026-11-12]. Mu3Lab already
  fails over inside LiteLLM, and it sets a 30 s read timeout. With the default retries, a
  stuck route could mean several 30 s waits.
- **Where:** `apps/livekit/voice-agent/src/agent.py`, the `AgentSession(...)` call.
- **What:** pass `conn_options=SessionConnectOptions(llm_conn_options=APIConnectOptions(max_retry=0 or 1, timeout=…))`
  (*check in docs*; this is how VoiceAgent does it). Keep LiteLLM as the only place that
  picks routes.
- **Done when:** with LiteLLM stopped mid-call, the log shows one failed attempt (or one
  retry), not four, and the person hears the slow-answer phrase (1.3) and then an apology,
  not silence.

### 1.2 Import the OpenAI client's chat modules in prewarm

- **Why:** the `openai` package imports its chat modules lazily on the first request. That
  blocked the event loop for **~140 ms** at the start of every call [measured].
- **Where:** `prewarm()` in `agent.py`.
- **What:** `import openai.pagination` and `import openai.resources.chat.completions.completions`
  inside `prewarm`.
- **Done when:** the first reply of a fresh job process is no slower than later ones (in
  `Reply after`).

### 1.3 Say something when the model is slow to start

- **Why:** Mu3Lab's outside routes take "anywhere from 1 to 60 s" (its own README). Silence
  sounds broken. VoiceAgent says "One moment, please." if the first word hasn't arrived
  after 1.5 s; the reply then follows in the same turn.
- **Where:** a new `llm_node` override on `Assistant` and `AppAssistant` (a shared mixin,
  like VoiceAgent's `CleanSpeech` in `my-agent/src/common.py`). Copy `with_wait_phrase` from
  there: it races the stream's first chunk against a timer and yields the phrase first if
  the timer wins, then cancels cleanly.
- **Settings:** add `wait_phrase` (text, empty = off) and `wait_after` (seconds, default 1.5)
  to `ctl/voice_settings.py` → `settings.json` → `load_settings()`.
- **Watch for:** the phrase becomes part of the reply's text in the history. That is fine,
  but check that tool fillers (`with_filler`) don't stack with it: both might speak on a
  tool turn.
- **Done when:** with a route forced slow, the person hears the phrase at ~1.5 s, then the
  answer; a unit test drives `with_wait_phrase` with a fake slow stream and checks both
  outcomes.

### 1.4 Clean the model's text before the voice and the history

- **Why:** LLMs write typographic characters (U+2011 non-breaking hyphen, U+202F narrow space
  before "p.m.", curly quotes). VoiceAgent's voice turned them into noise ("twenty-y fourth",
  real call 2026-11-21). Some models (gpt-oss, and others behind free routes) also glue two
  or three versions of the answer together ("…for you?Sure, what day…?No problem…").
- **Where:** the same `llm_node` mixin as 1.3.
- **What:** copy `my-agent/src/speech_text.py` (`clean_for_speech`, `ReplyCutter`) and
  `clean_reply` from `common.py`. Clean `ChatChunk.delta.content` so the history matches
  what was said. Kokoro may cope with more Unicode than Pocket did, so test which characters
  it mispronounces before you trim the map.
- **Done when:** unit tests for the character map and the cutter (copy VoiceAgent's), and a
  spoken check of "Tuesday the twenty‑fourth at 2:30 p.m." with typographic characters.

### 1.5 Log the turn detector's score on every turn

- **Why:** VoiceAgent's biggest delay was the end-of-turn wait. On one call, 6 of 7 turns
  hit `max_delay` because v1-mini scored even "Yes" and "nine" below its "finished"
  threshold (0.36) [measured 2026-11-10]. You can't tune responsiveness without the score.
- **Where:** copy `enable_eot_score_log()` and its two log filters from VoiceAgent's
  `agent.py`. LiveKit logs the score only at debug level, so the filters lift that one line
  to INFO and hide the other debug lines.
- **Done when:** each turn logs its score next to the `Reply after` line. Collect about 30
  household turns before touching 2.2.

## Phase 2: faster turns (measure first, then change)

### 2.1 Separate "speech ended" from "turn may end"

- **Why:** Mu3Lab uses 0.25 s for both VAD `min_silence_duration` and endpointing
  `min_delay`. With non-streaming Whisper, LiveKit cuts audio at each VAD stop and sends
  each piece to Whisper on its own. So a 0.25 s pause mid-sentence splits it into fragments,
  and each fragment is transcribed without the rest of the sentence. VoiceAgent split words
  this way at 0.3 s ("book an appo" / "intment") and settled on **0.5 s VAD silence** with
  0.5 s / 2.0 s endpointing.
- **What:** make VAD silence its own value (VoiceAgent: `vad.update_options(min_silence_duration=…)`
  per call, after prewarm loaded it). Try 0.4–0.5 s. Then check whether
  preemptive generation still hides the difference. Expose it in an **Advanced** part of
  Settings, Voice Assistant (operators only), next to `min_delay`, `max_delay` and the
  detector threshold (`unlikely_threshold`, *check in docs*). Keep the three
  responsiveness presets for everyone else.
- **Done when:** fewer split user messages in the history, and `Reply after` no worse on
  plain turns.

### 2.2 Tune responsiveness from the logged scores

- **Why:** VoiceAgent's 0.3 / 1.0 s cut callers off ("this upcoming Th" scored done and was
  answered 0.3 s later). Mu3Lab's "fast" preset (max 1.0 s) has the same risk.
- **What:** with 1.5's scores, set each preset's `min_delay` / `max_delay` / threshold from
  real turns. Use VoiceAgent's 0.5 / 2.0 s as the starting "balanced". Record the chosen
  numbers and the evidence here.

### 2.3 Streaming speech recognition with a flush on VAD stop

- **Why:** this is the largest gap. With REST Whisper, recognition starts after speech ends.
  With a streaming recognizer, the text is ready as the person stops: VoiceAgent's final
  arrives **~33 ms** after the flush [measured], against Mu3Lab's ~0.4 s.
- **Options, in order to try:**
  1. **Speaches' realtime / streaming transcription** with `use_realtime=True` in the openai
     STT plugin. Check that the installed Speaches version supports it, and how it ends a
     segment. Look for a way to force a final when VAD stops (VoiceAgent's trick:
     `user_state_changed` speaking → not speaking ⇒ flush).
  2. **VoiceAgent's sherpa-onnx server** (`VoiceAgent/stt-server/`, ~190 MB RAM, CPU only)
     plus its plugin (`my-agent/src/sherpa_stt.py`), as a second speech-recognition choice:
     "Fast (streaming)" vs "Most accurate (Whisper)". **The owner approved the extra
     container (2026-11-24).** Ship the **Apache-2.0 zipformer** model. Kroko is CC-BY-SA
     under a community licence, so ask the owner before adding it as a choice. Browser audio is wideband, so Whisper's accuracy edge may matter more
     here than on 8 kHz phone audio: compare both on household speech before choosing a
     default.
- **Where:** a new engine choice in `ctl/voice_settings.py`; the agent builds the STT from it
  per call. Option 2 would be a new service in `apps/livekit/docker-compose.yml` (needs the
  owner's OK; see the manifest's one-port rule in `docs/expansion/notes/livekit-voice.md`
  for internal-only services).
- **Done when:** `transcript` in `Reply after` drops below 0.1 s on plain turns, with word
  accuracy checked on ~20 household sentences.

### 2.4 Faster first audio

- **Why:** the person hears nothing until the first sentence is synthesized. VoiceAgent cut
  first audio from 0.66–1.03 s to ~0.49 s by synthesizing **clause by clause** (splitting at
  commas) instead of whole sentences [measured with Piper]. Pocket TTS streams inside a
  sentence: first audio 0.03 s on a desktop and 0.10–0.13 s on a 2-vCPU server.
- **What, smallest first:**
  1. Log per-sentence TTS timing like VoiceAgent's `pocket_voice.py` does: first audio,
     synthesis time and **mid-sentence gaps** (audio ready later than the audio before it
     finishes playing). Gaps are what make a voice sound broken.
  2. Give the TTS a tokenizer that sends the first clause early (a custom sentence tokenizer
     on LiveKit's TTS stream adapter, *check in docs*). Keep later clauses whole, so the
     voice keeps its sentence-level intonation.
  3. Check whether Speaches can stream Kokoro audio (chunked PCM while it generates). If it
     can, wire it up.
  4. **Pocket TTS as a second voice engine (approved by the owner, 2026-11-24)**
     (`VoiceAgent/my-agent/src/pocket_voice.py`). Offer it next to Kokoro in Settings, Voice
     Assistant. Code is MIT; the weights and voices are **CC BY 4.0, so credit Kyutai**
     wherever the voice is offered (the settings card and the README). It loads in ~8–9 s
     per process, so load it in prewarm.
     - **Plan for an NVIDIA card** (the owner's choice: assume one is there). The 2-vCPU
       server could only run the 7M model, but this machine can run the **standard English
       model** (219 MB), which sounds better. On this desktop's CPU it already measured
       first audio 0.09 s and a real-time factor of 0.29 [measured].
     - **Measure CPU against CUDA; don't assume the GPU wins.** For a small streaming model,
       moving work to the card can cost more than it saves (the same thing made Kokoro int8
       slow on the card). Pick the faster device as the default, and keep the other as a
       fallback for installs without a card.
     - **Image size.** CUDA PyTorch makes the agent image several GB larger than the CPU
       build (~1 GB). If CUDA isn't clearly faster, ship the CPU build. Check the size before
       committing to it.
     - **Graphics memory.** On the card, Pocket shares memory with Whisper, Kokoro and the
       local Qwen model (about 7 GB together today, on a 12 GB card). Check with
       `nvidia-smi` during a call that nothing spills to the processor.
- **Done when:** `first sound` in `Reply after` and the logged gaps are recorded before and
  after.

### 2.5 Check for a provider switch mid-conversation that breaks tool history

- **Why:** in VoiceAgent, Gemini rejected a request with **400 "Function call is missing a
  thought_signature"** when another model's tool calls were in the history, so a fallback
  after a tool call failed and the caller heard nothing [measured 2026-11-10]. Mu3Lab's
  routes go local Qwen → FreeLLMAPI (Gemini models) → any outside model, and app
  assistants make tool calls.
- **What:** test it. In an app assistant, make a tool call on the local route, then force
  the local route to time out on the next turn. If Gemini rejects the history, then either
  keep Gemini out of `mu3lab-voice` routes after the first, or drop tool-call items from the
  context sent to the fallback (LiteLLM may handle it; check).
- **Done when:** the result is written here, and either fixed or ruled out.

## Phase 3: a more natural voice

### 3.1 Give the model ready-to-say dates, times and numbers

- **Why:** a voice reads "2026-11-24T14:00:00-05:00" and "14:00" badly, and small models get
  calendar arithmetic wrong. VoiceAgent's tools return a ready-to-say `when` ("Tuesday the
  twenty-fourth at two PM") built in code (`speech_text.say_date` / `say_time`). The prompt
  also lists the next 14 days with their dates, so "next Thursday" is looked up, not
  computed.
- **Where:** Mu3Lab's app tools come from ContextForge, so their output can't be changed
  per tool. Do three things:
  1. Add a 14-day date list to `now_line()`.
  2. Add speaking rules to `INSTRUCTIONS`: say times as "two thirty PM", never read out
     IDs, links or raw ISO times, and round numbers when exactness doesn't matter.
  3. Optionally, in `ConfirmedToolset._wrap`, add a `when_spoken` field next to any
     ISO-8601 value in a tool result.
- **Done when:** a calendar app's events are read back naturally in a spoken test.

### 3.2 Don't say the same lead-in twice

- **Why:** in VoiceAgent, the model said its own lead-in and then the fixed transfer phrase
  also played (a real call on 2026-11-24). Mu3Lab's `AppAssistant.on_enter` says "Let me
  check {app}." and then calls `generate_reply()`, so the model may say "Sure, let me check
  your calendar…" too.
- **What:** listen for it first. If it happens, tell the app assistant in its first-turn
  instructions that the lead-in was already said, or let the model say it and drop the
  fixed line.

### 3.3 Interruptions that don't kill the answer

- **Why:** in a home, background speech, a TV or an "mm-hm" stops the assistant
  mid-sentence. VoiceAgent logs `agent_false_interruption` (*check in docs*: whether the
  session resumes speech after a false interruption, and the minimum interruption
  duration or word count).
- **What:** log false interruptions and mark interrupted assistant messages in the turn log
  (VoiceAgent prints `[INTERRUPTED]` and only the part that played). Then try resuming after
  a false interruption, plus a minimum interruption length. Expose any new knob only under
  Advanced.

### 3.4 Optional: a subtle "thinking" sound during tool calls

- LiveKit has a background-audio player with built-in clips such as keyboard typing
  (*check in docs*, and the clips' licence). It could replace or soften "One moment." on
  tool turns. Owner's choice; off by default.

## Phase 4: reliable changes (the most important correctness fix)

### 4.1 Replace the `YES` regex with a code-owned propose → read back → confirm

- **Why:** the current guard classifies meaning with a regex, and it gets real answers wrong:
  - "**Please** don't." passes (`please` is in the list).
  - "**Okay** wait, not that one." passes.
  - "**Right**, but make it Tuesday." passes, and runs the old arguments.
  - "Sounds good." and "Go for it." fail, so the person is asked again.
  - The yes is not tied to the arguments that were read back: the model can call the tool
    with different arguments after the yes.

  VoiceAgent's rule: the **model interprets meaning**, and **code owns state**. A change runs
  only when **a later user turn** answered **the latest proposal**, and only if that
  proposal's **read-back was heard in full**.
- **Design (for every non-read-only MCP tool, generic):**
  1. Calling a write tool does **not** run it. The wrapper stores a `PendingChange`:
     `action_id`, tool name, the exact arguments, a short summary, the user-turn count at
     proposal time. It returns facts: `{"status": "needs_confirmation", "action_id": …, "summary": …}`.
     The tool description tells the model to read the summary back and ask.
  2. One `confirm_change(action_id)` tool per app assistant. Code refuses (with a `ToolError`
     that says what to do) when:
     - the id is not the latest pending one (a new proposal replaces the old one);
     - no user turn has arrived since the proposal;
     - the read-back message was interrupted, so the person never heard all of it (track it
       on `conversation_item_added`: an assistant message with `interrupted=False` after the
       proposal, as in VoiceAgent's `attach_session_hooks`).
  3. On success, run the stored arguments (not new ones), clear the pending change, and
     return the tool's result. One confirm = one change.
  4. A changed request ("make it Tuesday") means the model proposes again. Code never
     decides whether words meant yes.
- **Where:** `ConfirmedToolset` in `agent.py` (replace `YES`, `ASK_FIRST` and `_used`). Keep
  per-session state in one object on `session.userdata`, separate from model-supplied facts.
- **Model size:** check that the local Qwen 3.5 4B reliably does the two-step dance. If it
  doesn't, simplify the tool descriptions before adding any code-side shortcuts.
- **Done when:** the tests in 5.1 pass, including the refusals.

### 4.2 Tools return facts; errors say what to do next

- Keep tool results compact (facts, ids, outcome) and let the model choose the words.
  `ToolError` messages should tell the model its next step ("propose the change again",
  "wait for their answer"). This is already true of `ASK_FIRST`; apply it to the new
  confirm errors too.
- Set `parallel_tool_calls=False` on the LLM. Small models make two conflicting calls in
  one turn otherwise (VoiceAgent sets it for every provider).

### 4.3 Silence: check in, then end the session

- **Why:** a browser tab left open keeps the agent, Whisper and the local model busy. On
  the GPU, that can hold models other apps want.
- **What:** VoiceAgent's `attach_silence_check`: `user_away_timeout` makes LiveKit report the
  person "away". The agent then re-asks up to N times (`generate_reply` with "briefly ask
  whether they are still there"), sleeping between them, says a goodbye and ends the
  session. Hearing the person speak cancels it. Mu3Lab settings: `silence_reask_after`
  (**default 30 s, chosen by the owner**; 0 = off), `silence_max_reasks` (1), `silence_goodbye`.
  Ending the session: *check in docs* for the cleanest way to leave a browser room
  (VoiceAgent deletes the room, since a phone call has no one left in it).
- **Done when:** the unit test from VoiceAgent (`_QuietSession` fake) passes, adapted.

## Phase 5: tests and diagnostics

### 5.1 A test suite for the agent

There is none today. Add `apps/livekit/voice-agent/tests/` (pytest, `pytest-asyncio`), run by
`make verify`, without network:

- `with_wait_phrase`, `clean_for_speech`, `ReplyCutter`: copy VoiceAgent's tests.
- The confirm guard (4.1): the full path (propose → user turn → confirm runs the stored
  arguments once). Pair it with each refusal: no later turn, a stale `action_id`, an
  interrupted read-back, and a second confirm of the same id. Use a fake MCP tool.
- The silence check with a fake session (copy `_QuietSession` from VoiceAgent's
  `tests/test_settings.py`).
- `load_settings()` with a missing file, a bad file and bad values: a bad file must never
  stop a call.
- Optional, needs a model: LiveKit's test session harness (`testing-livekit-agents` skill)
  for "asks before changing" and "hands over to the right app".

### 5.2 Per-call reports (the owner wants them on, 2026-11-24)

VoiceAgent writes one folder per call: `report.json` with the transcript, session events and
per-turn timing, plus the audio. That made each bad call easy to diagnose. The owner wants
these kept so problems can be found. They hold what people said at home, so:

- **on by default**, with one switch to turn them off;
- **audio as its own switch**, also on, so a sound problem can be heard;
- operators only: shown and downloadable in the dashboard, never to members;
- deleted automatically after a set time (suggest 14 days, adjustable);
- the setting says plainly that conversations are saved;
- stored in the LiveKit app's data folder, so they follow Mu3Lab's backup and removal rules
  (the app says `backup.include: false` today; decide whether reports count).

Copy `save_call_report` and the `record=` option from VoiceAgent's `agent.py`.
*Check in docs* how the session's own recording works in 1.8.8 when self-hosted (VoiceAgent:
`record={"audio": True, "transcript": True, "traces": False, "logs": False}`, nothing uploaded).

## What not to port

- SIP, FusionPBX, call transfer, `force_transfer_context`, phone-number caller ID, 8 kHz
  audio concerns.
- The dental booking flow, EasyAppointments, insurance, and the `TaskGroup` booking steps.
  Mu3Lab's handoff per app already gives each phase its own tools. Use a `TaskGroup` only if
  a future flow collects several fields in order.
- VoiceAgent's stdlib settings page and SSH tunnel: Mu3Lab has its dashboard and
  `ctl/voice_settings.py`.
- The `FallbackAdapter` provider chain inside the agent: LiteLLM already does this for
  Mu3Lab. Keep only 1.1.
- Kroko STT, unless the owner accepts its licence.

## Suggested order and sizes

| # | Item | Size | Risk |
| --- | --- | --- | --- |
| 1 | 1.1 retries, 1.2 prewarm import | S | low |
| 2 | 1.4 text clean-up, 1.3 wait phrase (+ settings) | M | low |
| 3 | 5.1 test scaffold with the tests for 1.3/1.4 | S | none |
| 4 | **4.1 confirm guard** + its tests | M | medium (model behaviour) |
| 5 | 1.5 score logging → 2.1 / 2.2 tuning | S + measuring | low |
| 6 | 2.5 mid-call fallback check | S | finds a bug or rules it out |
| 7 | 3.1 dates and speaking rules, 3.2 duplicate lead-in | S | low |
| 8 | 4.3 silence check | S | low |
| 9 | 5.2 per-call reports (early: they help diagnose everything after) | M | low |
| 10 | 2.3 streaming STT, 2.4 TTS first audio and Pocket TTS | L | medium |
| 11 | 3.3 interruptions, 3.4 sound | S each | optional |

## Decisions from the owner (2026-11-24)

1. **Streaming speech recognizer (2.3):** yes, as a new container in the LiveKit app. Ship
   zipformer (Apache-2.0); ask before adding Kroko.
2. **Pocket TTS (2.4):** yes, as a second voice engine. Assume an NVIDIA card is present,
   so use the standard model and measure CPU against CUDA.
3. **Silence (4.3):** check in after 30 s.
4. **Per-call reports (5.2):** yes, on by default, so problems can be diagnosed.

## Source files to read in VoiceAgent

| File | For |
| --- | --- |
| `my-agent/src/common.py` | `with_wait_phrase`, `clean_reply`, `CleanSpeech` (llm_node mixin), `SessionState`, `PendingAction` |
| `my-agent/src/speech_text.py` | `clean_for_speech`, `ReplyCutter`, `say_date` / `say_time` |
| `my-agent/src/agent.py` | `SessionConnectOptions`, `attach_session_hooks` (STT flush, read-back heard), `confirm_action`, `attach_call_logging`, `enable_eot_score_log`, `attach_silence_check`, `hang_up`, `prewarm` |
| `my-agent/src/sherpa_stt.py`, `stt-server/server.py` | streaming STT client and server, the flush protocol |
| `my-agent/src/pocket_voice.py` | in-process streaming TTS with per-sentence timing and gap logging |
| `my-agent/tests/` | tests to copy and adapt |
| `DEPLOY-PLAN.md` sections 0.1, 2, 3 | the measurements quoted above |
