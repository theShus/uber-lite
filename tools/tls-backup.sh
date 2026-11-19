#!/usr/bin/env bash
# tls-backup.sh <env>: save the gateway's certificate Secret to the state bucket before a teardown,
# so the next `make up` restores it instead of asking Let's Encrypt again (16.4 §2).
set -euo pipefail
ENV=${1:?usage: tls-backup.sh <qa|prod>}
cd "$(dirname "$0")/.."
BUCKET=$(grep bucket infra/terraform/backend.hcl | cut -d'"' -f2)

if ! secret=$(kubectl --context "$ENV" -n kong get secret aws-wildcard-tls -o json 2>/dev/null); then
  echo "== no aws-wildcard-tls Secret on $ENV: nothing to back up"; exit 0
fi
if ! jq -e '.metadata.annotations["cert-manager.io/issuer-name"]' <<<"$secret" >/dev/null; then
  echo "== aws-wildcard-tls wasn't issued by cert-manager (a placeholder?): not saved"; exit 0
fi
jq 'del(.metadata.uid, .metadata.resourceVersion, .metadata.creationTimestamp, .metadata.managedFields, .metadata.ownerReferences)' <<<"$secret" |
  aws s3 cp - "s3://$BUCKET/tls/$ENV/aws-wildcard-tls.json" --only-show-errors
echo "== saved kong/aws-wildcard-tls to s3://$BUCKET/tls/$ENV/"