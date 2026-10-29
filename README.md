# peat-db-tools

Tools for working with [Peat-DBase](https://gitlab.com/jormelton/peat-dbase)

All tools share one Python stack: a single uv project (`pyproject.toml`, `uv.lock`), ruff, and pytest.

## Tools

- [labeler/](labeler/) — map page and Lambda function for labeling cells of a
  7.5 arcsecond grid as peat / not peat, saved to a Google Sheet.

## Make

Run shared targets from the repo root:

- `make lint` — `ruff format` and `ruff check` over every tool
- `make test` — pytest over every tool's tests

Each tool has its own `Makefile` for build and deploy targets. Run them from the
root as `make TOOL-TARGET` (e.g. `make labeler-deploy`) or with
`make -C TOOL TARGET`. A tool's settings live in `TOOL/local.env`
(copy from `TOOL/local.env.example`).
