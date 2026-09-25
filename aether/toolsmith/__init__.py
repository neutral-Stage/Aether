"""Self-written tools: Aether writes a small tool when it lacks one, safely.

Propose → the user approves the exact capabilities → generate → static
check → an independent review that fails closed → install with a backup and
an audit entry → every run in a separate, sandboxed Python process →
results validated → at most two repairs, which can never widen what the
tool may do (Samuel's lifecycle, redesigned around the sandbox).
"""
