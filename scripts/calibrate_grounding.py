#!/usr/bin/env python3
"""Measure how well the vision model points at things on THIS Mac.

Takes a screenshot of the frontmost window's display, picks visible UI
elements whose true frames are known from the Accessibility tree, and asks the
vision model where each one is. Every answer is scored under both coordinate
conventions (image pixels vs a 0–1000 grid) and at several image sizes. The
best combination is written to <data_dir>/grounding_calibration.json, which
screen.grounding_settings() reads.

Run it on the Mac with a window full of labelled controls in front
(System Settings, Finder, Mail):

    python scripts/calibrate_grounding.py                   # measure + print
    python scripts/calibrate_grounding.py --write           # also save
    python scripts/calibrate_grounding.py --provider anthropic --edges 1366,1920

Needs Screen Recording + Accessibility permission and the provider's API key.
Costs one vision call per element per image size (default 20 × 3 = 60 calls).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.core.config import load_config
from aether.core.paths import data_dir
from aether.core.providers import create_client, merge_role_config
from aether.core.router import RouterConfig
from aether.perception import accessibility as ax
from aether.perception import grounding, screen

PROMPT = (
    "{label}\n\nWhere is the UI element labelled \"{name}\" ({role})? "
    "Reply with only JSON {{\"x\": <int>, \"y\": <int>}}: its center in this image's "
    "pixel coordinates. If it is not visible, reply {{\"x\": null, \"y\": null}}."
)


def pick_targets(disp: screen.DisplayInfo, n: int, seed: int) -> list[grounding.Target]:
    """Visible, labelled, reasonably sized elements on the captured display."""
    seen: set[str] = set()
    targets: list[grounding.Target] = []
    for el in ax.read_tree(max_elements=250, capture_handles=False):
        name = (el.title or el.value or "").strip()
        if not name or len(name) > 60 or name.lower() in seen:
            continue
        if el.w < 12 or el.h < 10 or el.w > disp.width * 0.8:
            continue
        cx, cy = el.x + el.w / 2, el.y + el.h / 2
        if not disp.contains(cx, cy):
            continue
        seen.add(name.lower())
        targets.append(grounding.Target(f"{name}|{el.role}", el.x, el.y, el.w, el.h))
    random.Random(seed).shuffle(targets)
    return targets[:n]


def vision_client(provider: str | None):  # noqa: ANN201
    cfg = load_config(validate=False)
    raw = RouterConfig.load(ROOT / "configs" / "router.yaml").raw
    if provider:
        role_cfg = dict((raw.get("providers") or {}).get(provider) or {})
        if not role_cfg:
            sys.exit(f"Unknown provider {provider!r} in configs/router.yaml")
    else:
        role_cfg = merge_role_config(raw, "vision")
        provider = str((raw.get("roles") or {}).get("vision", {}).get("provider", "vision"))
    client = create_client(role_cfg, api_keys=cfg.api_keys, role_name="vision")
    if client is None or not hasattr(client, "analyze_image"):
        sys.exit(f"No usable vision client for {provider!r} (API key set?)")
    return client, provider, str(role_cfg.get("model", ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--provider", help="provider template name (default: the vision role)")
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--edges", default="1280,1600,1920",
                    help="comma-separated long-edge sizes to try")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--write", action="store_true", help="save the best result")
    args = ap.parse_args()

    if not ax.available():
        sys.exit("Accessibility (pyobjc) unavailable — run on the Mac with permissions granted.")
    client, provider, model = vision_client(args.provider)
    disp = screen.default_display()
    targets = pick_targets(disp, args.samples, args.seed)
    if len(targets) < 5:
        sys.exit(f"Only {len(targets)} labelled elements visible; bring a busier window forward.")
    print(f"Calibrating {provider} ({model}) on display {disp.index} with "
          f"{len(targets)} elements.\n")

    scores: list[grounding.Score] = []
    for edge in [int(e) for e in args.edges.split(",") if e.strip()]:
        cap = screen.capture(disp, max_edge=edge)
        answers = []
        for t in targets:
            name, role = t.label.split("|", 1)
            prompt = PROMPT.format(label=cap.label(), name=name, role=role)
            try:
                reply = client.analyze_image(cap.path, prompt)
            except Exception as e:  # noqa: BLE001
                print(f"  ! {name}: {e}")
                reply = ""
            answers.append((t, grounding.parse_point(reply)))
        for space in grounding.COORD_SPACES:
            s = grounding.score(cap, answers, space, edge)
            scores.append(s)
            print(f"  edge {edge:>5}  {space:<9} hits {s.hits:>2}/{s.samples}"
                  f"  median error {s.median_error_pt:6.1f} pt")

    best = grounding.choose(scores)
    if best is None:
        return 1
    print(f"\nBest: coord_space={best.coord_space}, max_image_edge={best.max_image_edge} "
          f"({best.hit_rate:.0%} hits, median {best.median_error_pt:.1f} pt).")
    if best.hit_rate < 0.6:
        print("Warning: under 60% of points land on their element. Talk-mode pointing will lean "
              "on accessibility frames and the crop-refine pass; consider --provider anthropic.")
    if args.write:
        out = data_dir() / "grounding_calibration.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {**best.as_dict(), "provider": provider, "model": model,
                   "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "all": [s.as_dict() for s in scores]}
        out.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
