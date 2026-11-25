#!/usr/bin/env bash
# db-bootstrap.sh <env>: apply the RDS ExternalSecrets and (re)run the db-bootstrap Job.
# Re-run it after rotating a password.
set -euo pipefail
ENV=${1:?usage: db-bootstrap.sh <qa|prod>}
CTX=$ENV REGION=eu-central-1
cd "$(dirname "$0")/.."
k() { kubectl --context "$CTX" "$@"; }

# the master secret's ARN is new with every instance (and holds the account ID): not in Git
arn=$(aws rds describe-db-instances --region "$REGION" --db-instance-identifier "ul-$ENV" \
  --query 'DBInstances[0].MasterUserSecret.SecretArn' --output text)
case $arn in
  arn:aws:secretsmanager:*) ;;
  *) echo "✗ no master secret for ul-$ENV ($arn). Is the data layer up?"; exit 1 ;;
esac

# a Job's pod template is immutable: a re-run is a new Job
k -n data delete job db-bootstrap --ignore-not-found --wait
# fresh copies from AWS, so the Job never sets a password that was just rotated away
k -n data delete secret rds-master-secret db-users --ignore-not-found
# the Secrets dev-secrets.sh made with kubectl: ESO takes over these names
k -n uber-lite delete secret rider-api-db trip-service-db payment-service-db --ignore-not-found

k kustomize gitops/platform/secrets/db | sed "s|master-secret-arn-placeholder|$arn|" | k apply -f -

k -n data wait externalsecret/rds-master externalsecret/db-users --for=condition=Ready --timeout=2m
if ! k -n data wait job/db-bootstrap --for=condition=Complete --timeout=5m; then
  k -n data logs job/db-bootstrap --tail=30 || true
  exit 1
fi
k -n data logs job/db-bootstrap --tail=20
k -n uber-lite wait externalsecret/rides-db externalsecret/trips-db externalsecret/payments-db \
  --for=condition=Ready --timeout=2m
echo "== rides, trips, payments ready on ul-$ENV"
