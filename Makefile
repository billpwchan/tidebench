.PHONY: setup dev api web build check test browser-test

setup:
	uv sync --frozen
	npm --prefix frontend ci

dev:
	uv run python scripts/dev.py

api:
	uv run python -m tidebench

web:
	npm --prefix frontend run dev

build:
	npm --prefix frontend run build

test:
	uv run pytest -q

check:
	uv run ruff check backend tests scripts
	uv run ruff format --check backend tests scripts
	uv run pytest -q
	npm --prefix frontend run format:check
	npm --prefix frontend run build

browser-test:
	npm --prefix frontend run test:e2e
