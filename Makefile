.PHONY: dev test lint fmt deploy deploy-status

dev:
	uv run uvicorn library.main:app --reload --host 0.0.0.0 --port 8000

test:
	uv run coverage run -m pytest && uv run coverage report

lint:
	uv run ruff check . && uv run ruff format --check .

fmt:
	uv run ruff check --fix . && uv run ruff format .

# Deploy the promoted :latest image to the live host. Run only after main is
# green in CI (build + promote done). See docs/runbooks/deploy.md.
deploy:
	./scripts/deploy.sh

deploy-status:
	./scripts/deploy.sh --status
