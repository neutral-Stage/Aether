# Integrations

A catalog of five Apple apps Aether can read from and, with your approval,
write to: Calendar, Reminders, Contacts, Notes and Mail. No accounts, no
tokens, no sign-in — Aether reads these through macOS itself, the same way
any app you grant permission to would. Every integration is off until you
turn it on.

## What each one can do

| App | Read | Write |
|---|---|---|
| Calendar | Events in a date range, by title/location/notes | Add an event |
| Reminders | Reminders, optionally from one list | Add a reminder |
| Contacts | Look up a contact's phone, email or organization | — |
| Notes | Search notes by title or body | Create a note |
| Mail | Search the inbox (or a named mailbox) by subject/sender | — |

Calendar, Reminders and Contacts go through EventKit/Contacts, the same
frameworks Apple's own apps use, from inside the Aether app — that's what
shows the macOS permission prompt. Notes and Mail go through AppleScript,
run from the Aether sidecar.

With Calendar connected, the menu bar also shows your next meeting — see
[`MEETINGS.md`](MEETINGS.md#the-next-meeting-in-the-menu-bar).

## Connecting

Open the Aether window, expand **Integrations**, and press Connect next to
an app:

- **Calendar, Reminders, Contacts**: macOS shows its usual permission
  prompt. If you've previously said no, the row shows "Denied — allow in
  System Settings" with a button straight to the right Privacy pane.
- **Notes, Mail**: macOS shows an Automation prompt the first time (asking
  whether Aether can control that app). If you don't see it, check System
  Settings → Privacy & Security → Automation.

Disconnecting just tells Aether to stop using that app's tools — it does not
change or revoke the macOS permission itself, so reconnecting later doesn't
need you to grant it again.

## What leaves your Mac

Nothing extra. A tool's result (an event's title and time, a note's
snippet, a contact's email) goes to the model the same way any other tool
result does, as part of answering your request — it isn't sent anywhere
else, logged externally, or used to train anything. Results from Notes,
Mail, Contacts and Calendar notes are marked as untrusted data before the
model sees them, the same protection screen memory and web pages get, since
a note or email can contain someone else's text.

## Writes always ask first

Creating a calendar event, a reminder or a note always shows you an
editable draft before anything happens — title, time, location, notes, and
so on — so you can fix a detail or say no. There is no way to skip this
confirmation, including in careful mode being off. Calendar events are
created through EventKit, which cannot invite anyone; nothing is ever sent
to another person by these tools.

## Limits

- Not yet validated on a real Mac (see `docs/FIRST_RUN.md`) — the AppleScript
  for Notes and Mail in particular should be treated as best-effort until
  someone runs it against real data.
- Whether the Automation permission you grant Aether.app also covers
  `osascript` calls made from the sidecar process is unverified; if Notes/Mail
  tools fail with a permission error even after connecting, check System
  Settings → Privacy & Security → Automation for both entries.
- Mail search reads message content locally to build a snippet; it does not
  fetch attachments or download anything new.
- Contacts search returns only name, organization, emails and phone numbers
  — no photos, addresses or notes fields.
