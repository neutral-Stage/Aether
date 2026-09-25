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
| `allow_browsers` | none | Browsers to record even though their private windows can't be confirmed (see Limits), by bundle ID. Safari can also be turned on from the menu bar. |

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
- A password field detected by its accessibility subrole, not just its role,
  so a native secure text field is caught even where the role alone (as
  reported by some apps) would not show it.
- Chrome, Brave, Edge and Vivaldi windows: Aether asks the browser directly
  whether the front window is private, and skips it whenever that can't be
  confirmed (see Limits) — not just when it says yes.
- Firefox private and incognito windows, recognised by their titles, such as
  "Private Browsing" or "Incognito".
- Safari, and browsers Aether can't ask (Arc, Opera, Orion, DuckDuckGo):
  skipped outright, since there is no way to tell a private window from an
  ordinary one. Turn Safari back on from the menu bar, or list a bundle ID
  under `allow_browsers`, if you don't browse privately in it. Recording
  then relies on the window title, as it does for Firefox. A browser listed
  in `config.yaml` stays allowed until you remove it there; the menu toggle
  only adds or removes its own choice.
- Windows whose titles mention banking, passwords, passcodes, one-time or
  verification codes, credit cards, or signing in.
- The apps and window titles you exclude.
- The window is checked again right after its text is read, in case it
  switched to a different window, app or private mode while being read;
  a change drops the capture.

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
POST   /screen-memory/browsers       # {"bundle_id": "com.apple.Safari", "allowed": true}
```

`POST /screen-memory/browsers` works even while screen memory is off, since
hints use the same `allow_browsers` preference. `GET /screen-memory/status`
reports the current allowed set as `allow_browsers`.

Pauses, resumes, deletions and browser allow/disallow changes are written to
the audit log.

## Where it lives

`<data dir>/screen_memory.db`, a SQLite database with a full-text index,
readable only by your user account. It is not encrypted beyond what FileVault
provides. Nothing is sent anywhere unless you ask the agent about it; then
the matching snippets go to the model like any other tool result.

## Limits

- The front window is checked every 2 seconds. Aether does not yet listen for
  accessibility notifications, so a change and its capture can be up to a
  poll apart.
- The Chrome-family check trusts the browser's own answer to "is this window
  private?" (AppleScript's `mode of front window`). The first time, macOS
  asks whether Aether may control that browser; say yes, or the check always
  fails and the browser is always skipped. A window that can't answer in
  time (busy, hung, or the browser quit) is skipped, not recorded.
- Firefox, and an allowed Safari or other browser, are still recognised only
  by words in their titles, such as "Private Browsing", "Incognito" or
  "InPrivate" — there is no API to ask them directly. A window whose title
  doesn't say so is recorded like any other window.
- Only the front window is read. Text in background windows is not recorded.
- Meeting audio is not part of screen memory.
- Not yet tested on a real Mac.
