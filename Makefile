.PHONY: dev up down test lint fmt deploy deploy-status

dev:
	uv run uvicorn library.main:app --reload --host 0.0.0.0 --port 8000

# Bring the local stack up the way CI does: the four services that actually
# serve the app, with the commit stamped into the image.
#
# Why the explicit service list rather than a bare `docker compose up -d`:
# it omits `embedder`, for which TEI publishes no arm64 image, so this works
# on an Apple Silicon laptop. Semantic search is the only thing that needs it,
# and embedding failures are recorded-and-swallowed by design, so everything
# else — ingest, OCR, extraction, search by text — works without it. This is
# the same subset ci.yml's e2e job has been using all along.
#
# GIT_SHA makes /healthz report the commit instead of an empty string, so a
# local image is distinguishable from a broken deploy.
up:
	GIT_SHA=$$(git rev-parse --short HEAD) docker compose up -d --build db migrate api worker

down:
	docker compose down

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
