.PHONY: help install install-hooks init-keys test test-engine test-engine-live test-cli lint format dev-engine clean

help:
	@echo "snapd-invest — common commands"
	@echo ""
	@echo "  make install         Install dependencies for both stacks"
	@echo "  make install-hooks   Activate the repo-tracked git hooks"
	@echo "  make test            Run all tests (engine + CLI)"
	@echo "  make test-engine     Run Python engine tests only"
	@echo "  make test-cli        Run .NET CLI tests only"
	@echo "  make lint            Run lint checks for both stacks"
	@echo "  make format          Format code in both stacks"
	@echo "  make dev-engine      Start the Python engine with reload"
	@echo "  make clean           Remove build artifacts"

install: install-hooks
	cd engine && uv sync
	cd cli && dotnet restore

install-hooks:
	git config core.hooksPath scripts/git-hooks
	@echo "Git hooks activated from scripts/git-hooks/"

init-keys:
	cd engine && uv run python -m snapd_invest.tools.init_keys

test: test-engine test-cli

test-engine:
	cd engine && uv run pytest

test-engine-live:
	cd engine && SAXO_RUN_LIVE_TESTS=1 uv run pytest -m saxo_live -v

test-cli:
	cd cli && dotnet test

lint:
	cd engine && uv run ruff check && uv run ruff format --check && uv run mypy src
	cd cli && dotnet format --verify-no-changes && dotnet build /warnaserror --no-restore

format:
	cd engine && uv run ruff format && uv run ruff check --fix
	cd cli && dotnet format

dev-engine:
	cd engine && uv run uvicorn snapd_invest.api:app --reload --port 8000

clean:
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . -type d -name '.pytest_cache' -prune -exec rm -rf {} +
	find . -type d -name '.mypy_cache' -prune -exec rm -rf {} +
	find . -type d -name '.ruff_cache' -prune -exec rm -rf {} +
	find . -type d -name 'bin' -prune -exec rm -rf {} +
	find . -type d -name 'obj' -prune -exec rm -rf {} +
