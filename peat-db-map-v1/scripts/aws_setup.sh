#!/usr/bin/env bash
# One-time AWS setup: create the public-read site bucket.
# Upload the site afterward with `make site` and `make data-upload`.
# Run from anywhere: scripts/aws_setup.sh
set -euo pipefail

cd "$(dirname "$0")/.."
set -a
source local.env
set +a
export AWS_DEFAULT_REGION="$AWS_REGION"

# us-east-1 rejects an explicit LocationConstraint
if [ "$AWS_REGION" = us-east-1 ]; then
    aws s3api create-bucket --bucket "$SITE_BUCKET" > /dev/null
else
    aws s3api create-bucket --bucket "$SITE_BUCKET" \
        --create-bucket-configuration LocationConstraint="$AWS_REGION" > /dev/null
fi

# Allow a public bucket policy but no public ACLs
aws s3api put-public-access-block --bucket "$SITE_BUCKET" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false

mkdir -p build
sed "s|__BUCKET__|$SITE_BUCKET|" infra/site-policy.json > build/site-policy.json
aws s3api put-bucket-policy --bucket "$SITE_BUCKET" \
    --policy file://build/site-policy.json

echo "Created $SITE_BUCKET; site URL: $SITE_URL/index.html"
