.PHONY: dev up down test lint lint-actions check-docs fmt deploy deploy-status

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
	uv run ruff check . && uv run ruff format --check . && $(MAKE) lint-actions && \
	uv run python scripts/build_journal_index.py --check && uv run mypy && \
	$(MAKE) check-docs

# Documentation freshness. Now part of `lint`: it was held out while it reded the
# tree by design (a `make lint` that always fails is a `make lint` nobody runs),
# and the verify-and-stamp sweep cleared that, so it is a check you can actually
# run before pushing — which is the point, since it is the gate most likely to be
# tripped by an ordinary edit.
#
# The baseline must match the one in .github/workflows/ci.yml. The ratchet fails
# in BOTH directions, so a stale copy here reds even when the tree is clean —
# which is exactly how this was found after the sweep lowered ci.yml alone.
check-docs:
	uv run python scripts/check_docs.py --max-violations 0

# Regenerate the journal index. `--check` is in `lint` (and CI) with the same
# contract as `ruff format --check`: add an entry, re-run this, commit both.
journal-index:
	uv run python scripts/build_journal_index.py

# Lint the workflow files. Worth its own target because this is the one check
# that CANNOT be enforced from inside CI for the file it matters most for: an
# unparseable ci.yml means no job in ci.yml runs, so its own lint step never
# executes. Catching it locally is the only thing that works.
lint-actions:
	uv run actionlint -shellcheck= -pyflakes= .github/workflows/*.yml

fmt:
	uv run ruff check --fix . && uv run ruff format .

# Deploy the promoted :latest image to the live host. Run only after main is
# green in CI (build + promote done). See docs/runbooks/deploy.md.
deploy:
	./scripts/deploy.sh

deploy-status:
	./scripts/deploy.sh --status
