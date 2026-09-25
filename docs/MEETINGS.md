# Meeting notes

Aether can take notes of a call: it transcribes the meeting app's audio and
your microphone, and when you stop, it writes a summary, the decisions and the
action items. You start it yourself from the menu bar, and it asks first.

## Taking notes

1. Join the call, then choose **Take meeting notes…** in the menu bar.
2. Aether picks the meeting app: the one in front if it is Zoom, Teams,
   FaceTime, Webex, Slack or Discord, else one of those that is running, else
   the app in front (a browser with Google Meet, for example).
3. A prompt names the app, says where the audio is transcribed, and reminds
   you to tell the other people you're taking notes. Nothing is recorded
   unless you choose Start.
4. While it records, the menu bar icon shows a record mark and the menu shows
   the app and the elapsed time.
5. Choose **Stop and write notes**. The notes open in a panel with a Copy
   button.

Ask about past meetings in chat or by voice, for example "what were my action
items from the budget call?". The agent has `search_meetings` and
`meeting_notes`.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `meetings.transcription` | `local` | `local` transcribes on this Mac with mlx-whisper or pywhispercpp; `cloud` uses the `voice.stt` provider (Groq or OpenAI). |
| `meetings.summarize` | `true` | When the meeting ends, the model writes the notes from the transcript. |

Local transcription needs `pip install mlx-whisper` in the sidecar's
environment. Without it, Aether says so and does not start.

## What is kept, and where it goes

- **Audio:** captured in pieces of about 30 seconds, transcribed, and deleted.
  Audio is never stored. Quiet pieces are not sent at all.
- **Them and me:** the meeting app's audio is "Them" and your microphone is
  "Me". Aether can't tell the other people apart.
- **Transcript and notes:** kept in `<data dir>/meetings.db`, readable only by
  your user account. Delete a meeting with `DELETE /meetings/{id}`.
- **What leaves the Mac:** with `transcription: cloud`, the audio pieces go to
  your STT provider. With `summarize: true`, the transcript goes to your AI
  model when the meeting ends, with secrets redacted. The consent prompt says
  which of these apply.
- Starting, stopping and deleting meetings are written to the audit log.

## Sidecar API

```
GET    /meetings/transcription          # where audio goes; whether it can start
POST   /meetings                        # {"app": "zoom.us"} → {"id", "title", "engine"}
POST   /meetings/{id}/audio?channel=them|me&offset_s=30   # body: WAV
POST   /meetings/{id}/stop              # → {"text": notes, "notes": {...}}
GET    /meetings  ·  GET /meetings/{id}  ·  DELETE /meetings/{id}
```

## Limits

- Not yet tested on a real Mac or in a real call.
- The meeting app's audio comes from ScreenCaptureKit, which needs the Screen
  Recording permission. If it can't be captured, Aether records your
  microphone only and says so.
- After the Mac sleeps, capture restarts, at most five times per meeting.
- STOP does not end meeting notes; stop them from the menu bar.
- The next meeting from your calendar is not shown yet.
