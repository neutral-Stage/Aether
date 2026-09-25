# Live benchmark in throwaway macOS VMs

`python scripts/benchmark_tasks.py --vm` runs the 32 tasks in
[`tests/benchmark/live_tasks.yaml`](../tests/benchmark/live_tasks.yaml) against
the real agent. Each task runs in a fresh clone of a prepared ("golden") macOS
VM. The harness judges it by the machine's end state: a file on disk, the URL
Safari shows, the notes in Notes, or the answer Aether gave. The agent's own
claim of success does not count. The clone is deleted afterwards, so nothing
touches your own Mac.

The Phase B bar is **60% of tasks passing unattended**.

The harness uses [Lume](https://github.com/trycua/cua) (MIT) to clone and boot
VMs. It needs an Apple Silicon Mac with room for a ~60 GB VM.

## 1. Build the golden image (once)

1. Install Lume and create a macOS VM named `aether-golden`. Lume's README
   has the current commands, for example `lume create aether-golden --os macos
   --ipsw latest`. Give it 4 CPUs, 8 GB of memory and 60 GB of disk.
2. Boot it with `lume run aether-golden` and finish macOS setup. Create a user
   called `lume`, or change `ssh.user` in
   [`scripts/vm/lume.yaml`](../scripts/vm/lume.yaml).
3. Inside the VM:
   - Turn on **System Settings › General › Sharing › Remote Login**.
   - Add your key to `~/.ssh/authorized_keys`. On the host, first create a
     key with `ssh-keygen -t ed25519 -f ~/.ssh/aether_bench_ed25519`.
   - Clone Aether, then create its environment:
     `python3 -m venv .venv && .venv/bin/pip install -r requirements.lock`.
   - Put the model key in `.env`, for example `ZAI_API_KEY=…`. Use a key with a
     spending limit: the VM runs unattended.
   - In Terminal, run `scripts/vm/prime_golden.sh`. It installs a LaunchAgent
     that starts the sidecar at login with the token `aether-bench`. It also
     opens every app the checks query, so click **Allow** on each Automation
     prompt.
   - Under **Privacy & Security**, grant Accessibility and Screen Recording to
     the Python binary the LaunchAgent runs (`.venv/bin/python` resolves to
     it). Grant the same to Terminal. Then log out and back in.
   - Optional: add a Mail account so the `mail_draft` task can run. Pass
     `--have mail_account` when it exists.
4. Shut the VM down. It is now the golden image; clones start from it.

## 2. Run

From your Aether checkout on the host:

```bash
python scripts/benchmark_tasks.py --vm                         # every task, fresh clone each
python scripts/benchmark_tasks.py --vm --only safari_open,notes_create
python scripts/benchmark_tasks.py --vm --reuse-vm              # one clone for all tasks (faster)
python scripts/benchmark_tasks.py --vm --keep                  # leave clones for debugging
python scripts/benchmark_tasks.py --vm --out bench.json        # save the summary
```

Every task prints PASS or FAIL with the check it failed on. The command exits
non-zero below the 60% bar. Record the result table in
[`VALIDATION_LOG.md`](VALIDATION_LOG.md).

## 3. How a task is judged

| Check | Passes when |
|---|---|
| `applescript` + `expect` | The script's output contains the expected text (e.g. Safari's URL). |
| `shell` + `expect` | Same, for a shell command. |
| `frontmost` | That app is in front. |
| `file_exists`, `file_missing`, `file_contains` | The file system is in that state. |
| `answer_contains`, `answer_regex` | Aether's final answer says it (questions like "how many files…"). |
| `all` | Every listed check passes. |

No task asks Aether to delete, send or buy anything. Those actions need a
person to confirm, and nobody is watching the VM.

## Troubleshooting

- **Stuck at "did not come up"**: check `lume get <clone>` shows an IP address,
  and that SSH works with the key.
- **Every check fails**: the Automation prompts were not approved in the
  golden image. Run `prime_golden.sh` again inside it.
- **The agent never acts**: read `/tmp/aether-bench-sidecar.log` in the clone
  (use `--keep`). The usual causes are a missing API key or missing
  Accessibility permission.
- **A lume command changed**: edit the command templates in
  `scripts/vm/lume.yaml`. The harness reads them from there.
