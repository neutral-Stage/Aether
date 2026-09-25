"""Notes and Mail, read (and Notes, written) through AppleScript.

Every value from the model or the user goes in as an ``osascript`` argv
element (``run_applescript_args``), never spliced into the script source —
see ``aether/effectors/applescript.py``. Results come back as records joined
with the ASCII unit separator (``character id 31``) between fields and the
record separator (``character id 30``) between rows, which :func:`_split_records`
undoes; those control characters can't appear in ordinary note or mail text,
so there is no ambiguity to escape.
"""
from __future__ import annotations

import html

from ..effectors.applescript import AppleScriptResult, run_applescript_args

_TIMEOUT = 20
_UNIT_SEP = chr(31)
_RECORD_SEP = chr(30)
_AUTOMATION_HINT = (" Aether needs Automation permission for this app — allow it in "
                    "System Settings → Privacy & Security → Automation.")


class AppleScriptError(RuntimeError):
    """An AppleScript-backed Notes/Mail call failed; the message is user-facing."""


def _check(result: AppleScriptResult, what: str) -> str:
    """The script's stdout, or raise a clear, user-facing error."""
    if result.returncode == 0:
        return result.stdout
    stderr = (result.stderr or "").strip()
    if "-1743" in stderr:
        raise AppleScriptError(f"Couldn't {what}: not allowed.{_AUTOMATION_HINT}")
    if result.returncode == 124:
        raise AppleScriptError(f"Couldn't {what}: timed out.")
    raise AppleScriptError(f"Couldn't {what}: {stderr or 'AppleScript error'}")


def _split_records(raw: str) -> list[list[str]]:
    """Undo the ``character id 30`` / ``character id 31`` join the scripts use."""
    text = (raw or "").strip("\n")
    if not text:
        return []
    return [rec.split(_UNIT_SEP) for rec in text.split(_RECORD_SEP) if rec]


def _to_html(text: str) -> str:
    """Minimal, escaped HTML for a Notes body: newlines become ``<br>``."""
    return html.escape(str(text or "")).replace("\n", "<br>")


# --- Notes -----------------------------------------------------------------

_NOTES_SEARCH_SRC = """
on run argv
    set qry to item 1 of argv
    set lim to (item 2 of argv) as integer
    set usep to (character id 31)
    set rsep to (character id 30)
    set outList to {}
    tell application "Notes"
        if qry is "" then
            set matchedNotes to notes
        else
            set matchedNotes to (notes whose name contains qry or plaintext contains qry)
        end if
        set n to count of matchedNotes
        if n > lim then set n to lim
        repeat with i from 1 to n
            set theNote to item i of matchedNotes
            set nm to name of theNote
            set md to (modification date of theNote) as string
            set pt to plaintext of theNote
            if (length of pt) > 500 then set pt to text 1 thru 500 of pt
            set end of outList to (nm & usep & md & usep & pt)
        end repeat
    end tell
    set AppleScript's text item delimiters to rsep
    set outStr to outList as string
    set AppleScript's text item delimiters to ""
    return outStr
end run
"""

_NOTES_CREATE_SRC = """
on run argv
    set bodyHTML to item 1 of argv
    set folderName to item 2 of argv
    tell application "Notes"
        if folderName is not "" then
            try
                set targetFolder to folder folderName of default account
                tell targetFolder to make new note with properties {body:bodyHTML}
                return "OK"
            end try
        end if
        make new note at default account with properties {body:bodyHTML}
    end tell
    return "OK"
end run
"""


def notes_search(query: str, limit: int = 10) -> list[dict[str, str]]:
    """Notes matching ``query`` (name or body, case-insensitive) — name,
    modification date and a 500-char plaintext snippet each."""
    lim = max(1, min(int(limit or 10), 50))
    result = run_applescript_args(_NOTES_SEARCH_SRC, [str(query or ""), str(lim)],
                                  timeout=_TIMEOUT)
    raw = _check(result, "search Notes")
    rows = []
    for rec in _split_records(raw):
        if len(rec) < 3:
            continue
        rows.append({"name": rec[0], "modified": rec[1], "snippet": rec[2]})
    return rows


def notes_create(title: str, body: str, folder: str = "") -> str:
    """Create a note (``title`` on its own line, then ``body``) in ``folder``
    if it exists, else the default folder. Returns "OK" or raises."""
    body_html = f"<div><b>{_to_html(title)}</b></div><div>{_to_html(body)}</div>"
    result = run_applescript_args(_NOTES_CREATE_SRC, [body_html, str(folder or "")],
                                  timeout=_TIMEOUT)
    _check(result, "create the note")
    return "OK"


# --- Mail --------------------------------------------------------------

_MAIL_SEARCH_SRC = """
on run argv
    set qry to item 1 of argv
    set lim to (item 2 of argv) as integer
    set mbName to item 3 of argv
    set usep to (character id 31)
    set rsep to (character id 30)
    set outList to {}
    tell application "Mail"
        if mbName is "" then
            set targetMailbox to inbox
        else
            set targetMailbox to mailbox mbName
        end if
        if qry is "" then
            set matchedMsgs to (messages of targetMailbox)
        else
            set matchedMsgs to (messages of targetMailbox whose subject contains qry or sender contains qry)
        end if
        set n to count of matchedMsgs
        if n > lim then set n to lim
        repeat with i from 1 to n
            set msg to item i of matchedMsgs
            set subj to subject of msg
            set snd to sender of msg
            set dr to (date received of msg) as string
            set bodyText to content of msg
            if (length of bodyText) > 300 then set bodyText to text 1 thru 300 of bodyText
            set end of outList to (subj & usep & snd & usep & dr & usep & bodyText)
        end repeat
    end tell
    set AppleScript's text item delimiters to rsep
    set outStr to outList as string
    set AppleScript's text item delimiters to ""
    return outStr
end run
"""


def mail_search(query: str, limit: int = 10, mailbox: str = "") -> list[dict[str, str]]:
    """Messages in the inbox (or ``mailbox`` by name) whose subject or sender
    matches ``query`` — subject, sender, date received and a 300-char snippet
    each."""
    lim = max(1, min(int(limit or 10), 50))
    result = run_applescript_args(
        _MAIL_SEARCH_SRC, [str(query or ""), str(lim), str(mailbox or "")], timeout=_TIMEOUT)
    raw = _check(result, "search Mail")
    rows = []
    for rec in _split_records(raw):
        if len(rec) < 4:
            continue
        rows.append({"subject": rec[0], "sender": rec[1], "date": rec[2], "snippet": rec[3]})
    return rows
