# PeatDB interactive map: preprocess data and deploy to S3 (lint/test live at
# the root). Settings come from local.env (see local.env.example).
include local.env

export AWS_DEFAULT_REGION = $(AWS_REGION)

# map.js mirrors these preprocess settings; read them from the one source
config = $(shell sed -n 's/^$(1) = //p' scripts/config.py)

.PHONY: map-data render

## Data

# Rebuild from scratch so no stale chunks or zoom levels remain
map-data:
	rm -rf build/data
	uv run python scripts/preprocess.py ../data/Peat_DBase.csv build/data

## Site

# Assemble build/site/ with the same layout as the S3 bucket: index.html,
# map.js (with the MapTiler key and settings filled in) and data/.
# View locally: from build/site, run `npx http-server -p 8080 -c-1`
# Python http.server does not support range requests.
# rsync --delete drops data files a rebuild no longer makes (old chunks, zooms).
render:
	mkdir -p build/site
	cp web/index.html build/site/index.html
	sed -e 's|__MAPTILER_API_KEY__|$(MAPTILER_API_KEY)|' \
		-e 's|__CHUNK_SIZE__|$(call config,CHUNK_SIZE)|' \
		-e 's|__CLUSTER_MAX_ZOOM__|$(call config,CLUSTER_MAX_ZOOM)|' \
		-e 's|__CLUSTER_RADIUS_PX__|$(call config,CLUSTER_RADIUS_PX)|' \
		web/map.js > build/site/map.js
	rsync -a --delete build/data/ build/site/data/
