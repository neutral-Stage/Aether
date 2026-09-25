# Quick skills

A quick skill is a small job on a hotkey: a prompt, what it works on, and
where the answer goes. "Summarize the selection", "reply to this in my
voice and paste it", "explain this screen out loud".

| Field | Choices |
|---|---|
| Works on | the selection, the clipboard, the screen's text, a screenshot, words you speak, or nothing |
| Result goes to | pasted where you are, the clipboard, spoken, a small panel, or appended to a file |
| Hotkey | ⌃⌥1 to ⌃⌥9, or none |

- Manage them in the main window under "Quick skills", or run one from the
  menu bar. Four examples ship: Summarize selection (⌃⌥1), Reply in my voice
  (⌃⌥2), Explain this screen (⌃⌥3), Action items from clipboard.
- A skill that works on spoken words records on the first press of its
  hotkey and runs on the second.
- A skill only writes text; it never clicks, types elsewhere, or runs tools.
  What it reads is treated as material, not instructions, and secrets in it
  are redacted before it goes to the model.
- Skills never read or paste into password fields. File results must go
  inside `policy.approved_file_roots`.
- Skills live in `<data dir>/quick_skills.json`; the sidecar API is
  `GET/POST /quick-skills`, `DELETE /quick-skills/{id}` and
  `POST /quick-skills/{id}/run`.
