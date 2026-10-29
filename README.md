# peat-db-tools

Tools for working with [Peat-DBase](https://gitlab.com/jormelton/peat-dbase)

All tools share one Python stack: a single uv project (`pyproject.toml`, `uv.lock`), ruff, and pytest.

## Tools

- [labeler/](labeler/) — map page and Lambda function for labeling cells of a
  7.5 arcsecond grid as peat / not peat, saved to a Google Sheet.

## Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then
   run `uv sync` to create the virtualenv with the Python tools.
2. Install [Node.js](https://nodejs.org/), then run `npm install` to install
   [Prettier](https://prettier.io/) (pinned in `package.json`) for the HTML,
   CSS, and JavaScript.

## Make

Run shared targets from the repo root:

- `make lint` — `ruff format` and `ruff check` over Python, `prettier` over
  HTML, CSS, and JavaScript
- `make test` — pytest over every tool's tests

Each tool has its own `Makefile` for build and deploy targets. Run them from the
root as `make TOOL-TARGET` (e.g. `make labeler-deploy`) or with
`make -C TOOL TARGET`. A tool's settings live in `TOOL/local.env`
(copy from `TOOL/local.env.example`).
