# Proactive hints

Now and then, Aether can offer one short tip about what is on your screen: a
faster way to do something, something that looks wrong, an obvious next step,
or a risk. Each hint shows why it was offered. Hints are off until you turn
them on.

## Turning them on

Set `hints.enabled: true` in `config.yaml`. The app picks up the change
within a minute; no restart is needed.

| Setting | Default | Meaning |
|---|---|---|
| `min_confidence` | 0.75 | Hints the model is less sure about are dropped. |
| `idle_s` | 6 | Seconds without any input before Aether looks. |
| `gap_s` | 20 | The least time between two questions to the model. |
| `category_cooldown_min` | 15 | After a hint, its kind stays quiet this long. |
| `daily_cap` | 12 | The most hints shown in a day. |
| `max_queries_per_hour` | 30 | The most questions to the model in an hour. |

## When Aether asks

All of these must hold:

- Hints are on and you haven't reached the daily cap or hourly limit.
- You have not touched the keyboard or mouse for `idle_s`, and you are not
  typing.
- Aether is not busy: no task, no spoken answer, no guide, no dictation, and
  no confirmation waiting.
- The front window's text changed since the last question.
- The window may be read. Hints run through the exact same checks as screen
  memory (see docs/SCREEN_MEMORY.md), down to the browser private-window
  check: password managers, private browser windows, sign-in, banking and
  verification-code pages, password fields, Aether itself and the apps you
  excluded are never read. Pausing screen memory does not turn hints off, and
  allowing a browser (`allow_browsers`, or the menu bar's Safari toggle)
  applies to hints too.

## What is shown

The model must answer in a fixed format: whether a hint is needed, the hint in
at most 140 characters, its confidence, the reason, and one of four kinds.
Anything else is dropped. So is a hint that:

- is less confident than `min_confidence`,
- is of a kind you muted or that showed a hint recently,
- contains a link or a command to run, or reads like instructions to an
  assistant.

The card appears in the top-right corner, never takes focus, and closes after
14 seconds. "No more … tips" mutes that kind until you unmute it with
`POST /hints/mute` and `{"category": "...", "muted": false}`. STOP closes the
card. Every hint shown is written to the audit log.

## What leaves the Mac

When Aether asks, the front window's app name, title and up to 4,000
characters of its text go to the model, with secrets redacted and the text
marked as untrusted. Nothing is sent while the conditions above don't hold.
