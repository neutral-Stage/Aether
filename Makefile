PYTHON ?= python3.11

.PHONY: compile test benchmark redteam lint lint-fix ci swift schemas validate-packs lock

compile:
	$(PYTHON) -m compileall -q aether sidecar tests aether/plugins plugins

test:
	$(PYTHON) -m pytest tests/ -q

test-unit:
	$(PYTHON) -m pytest tests/unit tests/security -q

test-integration:
	$(PYTHON) -m pytest tests/integration -q

benchmark:
	$(PYTHON) scripts/benchmark_tasks.py --mock

redteam:
	$(PYTHON) -m tests.benchmark.redteam

doctor:
	$(PYTHON) -m aether.app --doctor

lint:
	ruff check aether sidecar tests scripts

lint-fix:
	ruff check aether sidecar tests scripts --fix

# Regenerate the pinned universal lockfile after editing any requirements*.txt.
lock:
	uv pip compile --universal --python-version 3.11 requirements.txt requirements-sidecar.txt -o requirements.lock

ci: compile lint test validate-packs benchmark swift

validate-packs:
	$(PYTHON) scripts/validate_packs.py

swift:
	cd macos/Aether && swift build && swift test

schemas:
	$(PYTHON) scripts/export_tool_schemas.py
