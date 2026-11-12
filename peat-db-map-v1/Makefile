# PeatDB interactive map: preprocess data and deploy to S3 (lint/test live at
# the root). Settings come from local.env (see local.env.example).
include local.env

export AWS_DEFAULT_REGION = $(AWS_REGION)

.PHONY: data-build

## Data

# Rebuild from scratch so no stale chunks or zoom levels remain
map-data:
	rm -rf build/data
	uv run python scripts/preprocess.py ../data/Peat_DBase.csv build/data
