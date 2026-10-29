#!/usr/bin/env bash
# One-time AWS setup: push the Lambda image, create the function with a public
# URL, create the website bucket, and upload the map page.
# Run from anywhere: scripts/aws_setup.sh
set -euo pipefail

cd "$(dirname "$0")/.."
set -a
source local.env
set +a
export AWS_DEFAULT_REGION="$AWS_REGION"
[ -n "${AWS_PROFILE:-}" ] || unset AWS_PROFILE

make push

# Lambda function, using the existing lambda-with-s3 role
account_id=$(aws sts get-caller-identity --query Account --output text)
image="$account_id.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:$FUNCTION_NAME"
aws lambda create-function --function-name "$FUNCTION_NAME" \
    --package-type Image --code ImageUri="$image" --architectures arm64 \
    --role "arn:aws:iam::$account_id:role/lambda-with-s3" \
    --timeout 15 --memory-size 256 \
    --environment "Variables={SHEET_ID=$SHEET_ID,SHOW_ALL_KEY=${SHOW_ALL_KEY:-}}" > /dev/null
aws lambda wait function-active-v2 --function-name "$FUNCTION_NAME"

# Public function URL that only the site may call from a browser
aws lambda create-function-url-config --function-name "$FUNCTION_NAME" \
    --auth-type NONE \
    --cors "{\"AllowOrigins\": [\"$SITE_URL\"], \"AllowMethods\": [\"POST\"], \"AllowHeaders\": [\"content-type\"], \"MaxAge\": 86400}" \
    > /dev/null
aws lambda add-permission --function-name "$FUNCTION_NAME" \
    --statement-id public-url --action lambda:InvokeFunctionUrl \
    --principal '*' --function-url-auth-type NONE > /dev/null
aws lambda add-permission --function-name "$FUNCTION_NAME" \
    --statement-id public-invoke --action lambda:InvokeFunction \
    --principal '*' --invoked-via-function-url > /dev/null

# Public-read website bucket; us-east-1 rejects an explicit LocationConstraint
if [ "$AWS_REGION" = us-east-1 ]; then
    aws s3api create-bucket --bucket "$SITE_BUCKET" > /dev/null
else
    aws s3api create-bucket --bucket "$SITE_BUCKET" \
        --create-bucket-configuration LocationConstraint="$AWS_REGION" > /dev/null
fi
aws s3api put-public-access-block --bucket "$SITE_BUCKET" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false
aws s3api put-bucket-website --bucket "$SITE_BUCKET" \
    --website-configuration '{"IndexDocument": {"Suffix": "index.html"}}'

mkdir -p build

sed "s|__BUCKET__|$SITE_BUCKET|" infra/site-policy.json > build/site-policy.json
aws s3api put-bucket-policy --bucket "$SITE_BUCKET" \
    --policy file://build/site-policy.json

make site
