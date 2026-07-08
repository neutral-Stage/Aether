# Knowledge Packs

A knowledge pack teaches Aether how to be **expert in a specific app** — its
shortcuts, multi-step recipes, gotchas, and preferred automation tier. Packs are
injected into the planner's context when that app is frontmost (or named), so the
agent uses the fast/reliable path instead of fumbling through the UI.

33 packs ship bundled (`aether/knowledge/packs/*.yaml`). You can add your own
without touching the codebase.

## Format

One YAML file per app, named by app key (e.g. `obsidian.yaml`):

```yaml
app: Obsidian            # display name (matched against frontmost app)
bundle_id: md.obsidian   # optional — more robust match than name
tier: 3                  # 1 = AppleScript/API, 2 = Accessibility, 3 = vision/keys
shortcuts:
  - "⌘O — Quick switcher"
  - "⌘P — Command palette"
recipes:                 # named multi-step playbooks the planner can follow
  new_note:
    - "⌘N for a new note"
    - "Type the title, then Enter"
  search_vault:
    - "⌘⇧F for global search"
gotchas:                 # quirks that trip up naive automation
  - "Electron app — AX tree is partial; prefer the command palette (⌘P)"
scripting:               # optional tier-1 hooks (AppleScript snippets/descriptions)
  open_daily_note: "Trigger the Daily Notes command via ⌘P"
```

Only `app` and `tier` are required. `validate_pack()` checks structure; run
`make validate-packs` (invoked by `make ci`).

### Choosing a tier
- **Tier 1** — the app has AppleScript/an API. Most reliable; prefer it.
- **Tier 2** — no scripting, but a good Accessibility tree (native Cocoa apps).
- **Tier 3** — canvas/Electron/games where AX is weak; rely on key commands +
  vision. Add a `background_safe: false` note in `gotchas` if the app needs focus.

## Sideloading your own packs

Drop `.yaml` files in the sideload directory — no rebuild needed. Resolution
order (first wins):

1. `AETHER_PACKS_DIR` environment variable
2. `knowledge.sideload_dir` in `config.yaml`
3. default `~/.aether/packs`

Sideloaded packs override bundled ones with the same app key, so you can refine a
shipped pack locally.

## Learned recipes (write-back)

With `knowledge.learn: true` (default), a **successful** run in an app distills
its tool sequence into a named recipe and appends it to
`<sideload>/learned/<app_key>.yaml`. The loader merges these into the pack
context, so the next time that app is frontmost the model sees "recipes you've
done before" alongside the bundled knowledge — static packs become learned
expertise. Learned recipes are per-app, deduplicated, and capped (newest kept);
they never overwrite the bundled pack. Delete `<sideload>/learned/` to reset.

## Proactive triggers

`watch_app(app, when=…, then_goal=…, auto=…)` sets a **proactive trigger**: when
the watched app changes and the change text contains `when`, Aether fires
`then_goal`. By default it **suggests** ("Xcode changed — want me to run the
tests?"); it only auto-runs when both `app_watcher.auto_run_triggers: true` and
the trigger's `auto=true` — auto-acting on a screen change is deliberately
opt-in (see the Phase 9 safety model).

## Sharing packs

Packs are plain data — no code — so they're safe to share. To contribute one
upstream, add the `.yaml` to `aether/knowledge/packs/`, confirm `make
validate-packs` passes, and open a PR. A community pack index is the planned next
step; until then, share via the sideload directory or PRs.
