# Calls

The voice agent adds one line to `calls.jsonl` here when a call starts and one when it ends: the
room, the caller's sign-in email and the time. Never anything that was said. Mu3Lab's Home widget
for LiveKit (`apps/livekit/widgets.py`) reads it for the calls today and the last few calls.

The agent runs as Mu3Lab's own user (its build argument `UID` in `docker-compose.yml`) so it can
write here. Nothing here is checked in except this note.
