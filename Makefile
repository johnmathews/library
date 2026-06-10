.PHONY: dev test lint fmt

dev:
	uv run uvicorn library.main:app --reload --host 0.0.0.0 --port 8000

test:
	uv run coverage run -m pytest && uv run coverage report

lint:
	uv run ruff check . && uv run ruff format --check .

fmt:
	uv run ruff check --fix . && uv run ruff format .
