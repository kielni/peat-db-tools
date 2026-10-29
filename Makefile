# Peat DB tools: shared lint/test; each tool has its own Makefile.
# Run a tool's target from here as TOOL-TARGET, e.g. `make labeler-deploy`.

TOOLS = labeler

.PHONY: lint test

lint:
	uv run ruff format .
	uv run ruff check .
	npx prettier --write --log-level warn "**/*.{html,css,js}"

test:
	uv run pytest

$(addsuffix -%,$(TOOLS)):
	$(MAKE) -C $(firstword $(subst -, ,$@)) $*
