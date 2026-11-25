#!/usr/bin/env bash
# tls-restore.sh <env>: put the saved certificate Secret back BEFORE the Gateway is applied,
# so cert-manager finds a valid certificate and doesn't order a new one (16.4 §2).
set -euo pipefail
ENV=${1:?usage: tls-restore.sh <qa|prod>}
cd "$(dirname "$0")/.."
BUCKET=$(grep bucket infra/terraform/backend.hcl | cut -d'"' -f2)
KEY="tls/$ENV/aws-wildcard-tls.json"

if ! aws s3api head-object --bucket "$BUCKET" --key "$KEY" >/dev/null 2>&1; then
  echo "== no saved certificate for $ENV: cert-manager will issue a new one"; exit 0
fi
kubectl --context "$ENV" get namespace kong >/dev/null 2>&1 || kubectl --context "$ENV" create namespace kong
aws s3 cp "s3://$BUCKET/$KEY" - --only-show-errors | kubectl --context "$ENV" apply -f -
echo "== restored kong/aws-wildcard-tls from s3://$BUCKET/$KEY"
