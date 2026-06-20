.PHONY: compile test benchmark lint lint-fix ci swift schemas validate-packs

compile:
	python -m compileall -q aether sidecar tests aether/plugins plugins

test:
	python -m pytest tests/ -q

test-unit:
	python -m pytest tests/unit tests/security -q

test-integration:
	python -m pytest tests/integration -q

benchmark:
	python scripts/benchmark_tasks.py --mock

lint:
	ruff check aether sidecar tests

lint-fix:
	ruff check aether sidecar tests --fix

ci: compile lint test validate-packs benchmark swift

validate-packs:
	python scripts/validate_packs.py

swift:
	cd macos/Aether && swift build && swift test

schemas:
	python scripts/export_tool_schemas.py
