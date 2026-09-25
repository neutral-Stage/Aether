# Self-written tools

When no tool fits a task, Aether can write a small one for itself: merge CSV
files, batch-rename by a pattern, pull links out of saved web pages, total up
an export. You approve every new tool before it exists, and every run of it is
sandboxed to exactly what you approved.

## What happens

1. **Aether proposes a tool** with `make_tool`: a name, what it does, its
   inputs, and what it needs to access.
2. **You approve or decline** a plain-language list, for example:

   ```
   🛠 Create a new tool: my_merge_csvs
   What it does: Merges every CSV file in a folder into one file.
   Inputs: folder (string), output (string)
   It:
     • no internet
     • can read your files, except passwords, keys and browser data
     • can write in ~/Documents/Merged
     • cannot start other programs, control apps or change settings
   ```

3. **Aether writes the code**, a checker rejects anything outside the rules,
   and a separate review of the code approves or rejects it. The review never
   sees your conversation, and anything but a clear approval is a rejection.
4. **The tool is installed** as `my_<name>` and Aether uses it like any other
   tool.
5. **Each run is its own Python process in the macOS sandbox.** If the tool
   has a bug, Aether fixes the code (at most twice per task) and tries again.
   A fix never changes what the tool may access. To get more access, Aether
   has to propose a new version and ask you again.

## Limits every tool runs under

- Writes only in a fresh scratch folder and the folders you approved.
- No internet unless you approved it. A tool with internet access can read
  only the folders it declared.
- Cannot start other programs, send Apple Events, open apps, or signal other
  processes.
- Cannot read credential stores (SSH keys, cloud credentials, keychains,
  browser profiles) or Aether's own data folder.
- 60 seconds per run by default, plus CPU and file-size limits.
- Standard-library Python only.

Tools do not run at all without the macOS sandbox.

## Managing your tools

Installed tools live in `<data dir>/tools/<name>/` with the last five
versions kept in `history/`. The sidecar API lists and manages them:

| Request | What it does |
|---|---|
| `GET /toolsmith/tools` | List tools, their access and versions |
| `GET /toolsmith/tools/{name}` | Show one tool, including its code |
| `POST /toolsmith/tools/{name}/rollback` | Go back to the previous version |
| `DELETE /toolsmith/tools/{name}` | Remove it (moved to `tools/.removed/`) |

A tool whose code was edited outside Aether no longer loads. Every install,
run, repair, removal and rollback is recorded in the audit log.

## Settings

```yaml
toolsmith:
  enabled: true
  timeout_s: 60                        # per run, max 300
  max_repairs: 2                       # automatic fixes per tool per task (0-2)
  restrict_reads_with_network: true    # internet tools read only their folders
  extra_read_roots: []                 # e.g. a Python installed somewhere unusual
```

If internet tools fail at start with a permission error, your Python may be
installed outside the usual places. Add its folder to `extra_read_roots`.
