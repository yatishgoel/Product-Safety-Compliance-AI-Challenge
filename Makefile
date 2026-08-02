.PHONY: setup ui-setup ui ui-dev index refresh evaluate check test serve corpus report engines \
        docker docker-run env

# Load .env if present, so keys survive a new terminal instead of needing a fresh export
# every time. .env is gitignored; copy .env.example to start one.
ifneq (,$(wildcard .env))
include .env
export
endif

# Forwarded into the container. Names only, never values.
DOCKER_ENV = -e GEMINI_API_KEY -e GEMINI_MODEL -e JUDGE_MODEL \
             -e PHOENIX_API_KEY -e PHOENIX_COLLECTOR_ENDPOINT -e PHOENIX_PROJECT_NAME

setup:
	uv sync --all-extras

# Only needed to change the frontend. The production build is committed, so the API and the
# container serve the UI without node installed at all.
ui-setup:
	cd ui && npm install

ui: ui-setup
	cd ui && npm run build

ui-dev: ui-setup
	cd ui && npm run dev

index:
	uv run python -m app.index

refresh:
	uv run python -m app.index --refresh

evaluate:
	uv run python -m app.evaluate $(FILE)

check:
	uv run python -m app.evaluate --check

test:
	uv run pytest -q

serve:
	uv run uvicorn app.api:api --reload --port 8000

corpus:
	uv run python -m eval.generate --clean

report: corpus
	uv run python -m eval.report --failures

engines:
	uv run python -m app.extract --check --only tesseract

docker:
	docker build -t compliance .

docker-run: docker
	docker run --rm -p 8000:8000 $(DOCKER_ENV) compliance

# Which optional tiers are actually switched on right now.
env:
	@printf '  %-28s %s\n' \
	  "GEMINI_API_KEY" "$(if $(GEMINI_API_KEY),set, not set - vision tier off)" \
	  "PHOENIX_COLLECTOR_ENDPOINT" "$(if $(PHOENIX_COLLECTOR_ENDPOINT),set, not set - tracing off)" \
	  "PHOENIX_API_KEY" "$(if $(PHOENIX_API_KEY),set, not set)" \
	  "GEMINI_MODEL" "$(if $(GEMINI_MODEL),$(GEMINI_MODEL), default gemini-3-flash-preview)"
