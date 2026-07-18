# uv's default cache (~/.cache/uv) is read-only in some sandboxed/ro-root
# environments, which makes every `uv run`/`uv sync` fail at startup. Probe it
# and redirect to a writable /tmp/uv-cache ONLY when the default is unwritable;
# on a normal writable machine UV_CACHE_DIR stays empty (unset) so uv uses its
# own resolution (honoring XDG_CACHE_HOME) unchanged. Override explicitly with
# `make ... UV_CACHE_DIR=/path` or the env var.
UV_CACHE_DIR ?= $(shell \
	d="$$HOME/.cache/uv"; \
	if mkdir -p "$$d" 2>/dev/null && touch "$$d/.uvwtest" 2>/dev/null; then \
		rm -f "$$d/.uvwtest"; echo; \
	else \
		echo "/tmp/uv-cache"; \
	fi)
ifneq ($(strip $(UV_CACHE_DIR)),)
export UV_CACHE_DIR
endif

install:
	uv sync

dev:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --reload

gateway:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001

test:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/ -v

test-blocking-io:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/blocking_io -q --tb=short

lint:
	uvx ruff check .
	uvx ruff format --check .

format:
	uvx ruff check . --fix && uvx ruff format .

detect-blocking-io:
	@PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run python ../scripts/detect_blocking_io_static.py --output ../.deer-flow/blocking-io-findings.json

# Generate a new alembic revision by autogenerating the diff against the live
# ORM models. Usage: make migrate-rev MSG="add foo column to runs"
# The Gateway runs `alembic upgrade head` automatically at startup via
# `bootstrap_schema`, so there is intentionally no `migrate` / `migrate-stamp`
# target -- the single execution path keeps ops mistakes off the table.
# The script builds a fresh temp SQLite at head, then diffs the live models
# against it, so a clean checkout does NOT need a pre-existing ./data/deerflow.db.
migrate-rev:
	@if [ -z "$(MSG)" ]; then echo 'usage: make migrate-rev MSG="describe the change"'; exit 1; fi
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run python scripts/_autogen_revision.py "$(MSG)"
