# Peat labeler: Google Sheet tasks and AWS deploy (lint/test live at the root).
# Settings come from local.env (see local.env.example).
include local.env

export SHEET_ID SITE_URL
export AWS_DEFAULT_REGION = $(AWS_REGION)
ifdef AWS_PROFILE
export AWS_PROFILE
endif

ACCOUNT_ID = $(shell aws sts get-caller-identity --query Account --output text)
REGISTRY = $(ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com
# Tagged by function name so other images in the existing repository are untouched
IMAGE = $(REGISTRY)/$(ECR_REPO):$(FUNCTION_NAME)

REQUIRED = AWS_REGION FUNCTION_NAME ECR_REPO SITE_BUCKET SITE_URL SHEET_ID \
	MAPTILER_API_KEY

.PHONY: check-env keys build push deploy \
	render site url logs

## Google Sheet

keys:
	uv run python scripts/make_keys.py


## AWS

build:
	cp service-account.json lambda/
	uv export --frozen --no-dev --no-emit-project -o lambda/requirements.txt > /dev/null
	docker buildx build --platform linux/arm64 --provenance=false --load \
		-t $(ECR_REPO) lambda

push: build
	aws ecr get-login-password | docker login --username AWS --password-stdin $(REGISTRY)
	docker tag $(ECR_REPO) $(IMAGE)
	docker push $(IMAGE)

deploy: push
	aws lambda update-function-code --function-name $(FUNCTION_NAME) \
		--image-uri $(IMAGE) > /dev/null
	aws lambda wait function-updated-v2 --function-name $(FUNCTION_NAME)

render:
	$(if $(API_URL),,$(error API_URL is not set in local.env; copy the Function URL from the Lambda console))
	mkdir -p build
	cp web/index.html build/index.html
	sed -e 's|__MAPTILER_API_KEY__|$(MAPTILER_API_KEY)|' -e 's|__API_URL__|$(API_URL)|' \
		web/map.js > build/map.js

site: render
	aws s3 cp build/index.html s3://$(SITE_BUCKET)/index.html \
		--content-type text/html --cache-control no-cache
	aws s3 cp build/map.js s3://$(SITE_BUCKET)/map.js \
		--content-type text/javascript --cache-control no-cache


logs:
	aws logs tail /aws/lambda/$(FUNCTION_NAME) --follow
