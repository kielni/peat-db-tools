# Peat DB tools: shared lint/test. Each tool has its own independent
# Makefile; run its targets from inside the tool's directory.

.PHONY: lint test

lint:
	uv run ruff format .
	uv run ruff check .
	npx prettier --write --log-level warn "**/*.{html,css,js}"

test:
	uv run pytest
