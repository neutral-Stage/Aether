# Validation log

Results of running Aether against a real Mac, following
[`FIRST_RUN.md`](FIRST_RUN.md). Mock-only test runs do not belong here. Newest first.
Paste the table that `scripts/live_smoke.py --tasks` saves, plus the machine
details. For the unattended benchmark in throwaway VMs
(`scripts/benchmark_tasks.py --vm`, [BENCHMARK_VM.md](BENCHMARK_VM.md)), paste
its pass rate against the 60% bar and the failing task ids.

Template:

```
## YYYY-MM-DD — <Mac model>, macOS <version>, <brain model>

- Build: `make app` at <commit>, identity: Aether Dev | ad-hoc
- Doctor: <verdict>, notable warnings: …
- Grounding calibration: <coord_space> @ <edge> px, <hit rate>, median <n> pt

| Check | Result | Detail |
|---|---|---|
| … | … | … |

Notes: what failed, why, what was changed.
```

## Log

_No real-Mac runs recorded yet._
