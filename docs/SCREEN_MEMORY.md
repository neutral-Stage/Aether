# Screen memory

Screen memory remembers the text of what you look at, on this Mac only, so you
can ask "what was that article about solar panels this morning?" or "find the
booking number from the email earlier". It is off until you turn it on.

## Turning it on

Set `screen_memory.enabled: true` in `config.yaml` and restart the sidecar.
The menu bar icon changes to an eye while screen text is being remembered.

| Setting | Default | Meaning |
|---|---|---|
| `retention_days` | 14 | Older captures are deleted automatically. |
| `interval_s` | 30 | A window you stay in is re-read this often and kept only if its text changed. |
| `ocr_fallback` | true | When an app shows almost no text to accessibility, read the front window with on-device OCR. |
| `exclude_bundle_ids` | none | Apps never to record, by bundle ID. |
| `exclude_window_globs` | none | Window titles never to record, for example `"*medical*"`. |
| `only_bundle_ids` | none | When set, only these apps are recorded. |

## What is kept

- Text only. No screenshots or video are stored. When OCR is used, the
  cropped image of the front window is read and then discarded.
- For each capture: the time, the app, the window title, the text, and
  whether it came from accessibility or OCR.
- A window is recorded after it has stayed in front for 1.5 seconds, so
  windows you pass through while switching are not.
- Secrets are redacted before anything is stored, with the same rules the
  agent uses before calling a model.
- The same text in the same window is stored at most once an hour.

## What is never recorded

The rules fail closed: if Aether can't tell what is in front, it records
nothing.

- Anything while paused.
- An app or window it can't identify.
- A window while a password field in it has focus. Password fields
  elsewhere in a window are left out of its text.
- Aether's own windows.
- Password managers: 1Password, Bitwarden, Keychain Access, Passwords,
  KeePassXC, Dashlane, LastPass, Enpass and NordPass.
- Private and incognito browser windows whose titles say so (see Limits).
- Windows whose titles mention banking, passwords, passcodes, one-time or
  verification codes, credit cards, or signing in.
- The apps and window titles you exclude.

## Asking about it

The agent has three tools: `search_screen`, `screen_memory_detail` and
`activity_summary`. Ask in chat or by voice, for example "what did I work on
this afternoon?". Recalled text is marked as untrusted data, and text that
tries to give the agent instructions puts the run under the usual stricter
confirmation rules.

The tools are not offered to coding agents over Aether's MCP server unless
you add them to `mcp_server.expose_tools`.

## Pausing and deleting

The menu bar shows the status and has Pause or Resume, Delete last hour, and
Delete all. A pause survives a sidecar restart; only Resume ends it. The same
controls are in the sidecar API:

```
GET    /screen-memory/status
POST   /screen-memory/pause
POST   /screen-memory/resume
DELETE /screen-memory?minutes=60     # leave out minutes to delete everything
GET    /screen-memory/search?q=...&hours=24&app=Safari
GET    /screen-memory/activity?hours=8
```

Pauses, resumes and deletions are written to the audit log.

## Where it lives

`<data dir>/screen_memory.db`, a SQLite database with a full-text index,
readable only by your user account. It is not encrypted beyond what FileVault
provides. Nothing is sent anywhere unless you ask the agent about it; then
the matching snippets go to the model like any other tool result.

## Limits

- The front window is checked every 2 seconds. Aether does not yet listen for
  accessibility notifications, so a change and its capture can be up to a
  poll apart.
- Private windows are recognised only by words in their titles, such as
  "Private Browsing", "Incognito" or "InPrivate". A browser that doesn't put
  this in the window title is recorded like any other window. If that
  matters, exclude the browser or pause before browsing privately.
- Only the front window is read. Text in background windows is not recorded.
- Meeting audio is not part of screen memory.
- Not yet tested on a real Mac.
