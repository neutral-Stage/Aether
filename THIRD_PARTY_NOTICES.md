# Third-party notices

Aether's code is its own. Where a design follows another project, it is
listed here. When code is adapted, the file keeps the original copyright and
license notice and the project is listed under "Adapted code".

## Adapted code

None so far.

## Designs followed (reimplemented, no code copied)

| Where in Aether | Follows | License of the source |
|---|---|---|
| `aether/effectors/targeting.py`, `aether/tools/targeting_tools.py`: click by name on a fresh accessibility tree, the ladder AX → OCR text → numbered marks → coordinates | cursor-voice | MIT |
| `aether/effectors/targeting.py`: refuse a retargeted click whose label differs; money gate in `aether/core/policy.py` | avatar-cursor | MIT |
| `aether/perception/screen.py`: label every screenshot with its pixel size, display and cursor; hide Aether's own windows | farzaa/clicky | MIT |
| `aether/effectors/clipboard.py`: restore the clipboard only if nothing else changed it | yoclicky | MIT |
| `aether/effectors/sandbox.py`: macOS Seatbelt profile per command, roots passed as `-D` parameters | OpenAI Codex CLI | Apache-2.0 |
| `aether/effectors/sandbox.py`: `(allow default)` then deny writes outside the roots | Bazel's macOS sandbox | Apache-2.0 |
| `aether/perception/grounding.py`: measure the model's coordinate convention instead of assuming it | UI-TARS Desktop | Apache-2.0 |
| `aether/toolsmith/`: propose → approve → generate → review → install → validate → repair lifecycle for tools the assistant writes itself (redesigned: the review fails closed and the tools run in a sandbox) | Samuel (screen-voice-agent) | MIT |
