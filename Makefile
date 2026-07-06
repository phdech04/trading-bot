# Common commands. `make install` first, then e.g. `make backtest`.

.PHONY: install test lint fmt backtest paper paper-execute live kill

install:        ## install/sync all dependencies into .venv (requires uv)
	uv sync

test:           ## run the test suite
	uv run pytest

lint:           ## check code style + formatting (no changes made)
	uv run ruff check .
	uv run ruff format --check .

fmt:            ## auto-fix style + formatting
	uv run ruff check --fix .
	uv run ruff format .

backtest:       ## run the placeholder backtest (cached data, else synthetic)
	uv run python scripts/run_backtest.py

paper:          ## paper trading, DRY-RUN (logs intended orders only)
	uv run python scripts/run_paper.py

paper-execute:  ## paper trading, actually placing paper orders
	uv run python scripts/run_paper.py --execute

live:           ## live trading -- guarded: env flag + typed confirmation
	uv run python scripts/run_live.py

kill:           ## EMERGENCY: cancel all orders + close all positions
	uv run python scripts/kill.py
