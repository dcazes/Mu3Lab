# Call reports

After each call the voice agent saves one folder here, named by the time and the room:

- `call.json`: who called, when it started and ended, and whether there is audio;
- `report.json`: LiveKit's session report: every message, tool call and turn timing;
- `audio.ogg`: the call's audio, unless the household switched audio off.

They hold what people said at home. Only Mu3Lab's administrators can see them, in Settings, Voice
assistant, and opening one is noted in Mu3Lab's activity log. They are deleted after the number of
days chosen there (14 by default), are never backed up, and go when LiveKit is uninstalled.
Switch reports off there to keep none. Nothing here is checked in except this note.
