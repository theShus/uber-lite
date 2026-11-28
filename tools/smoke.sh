#!/usr/bin/env bash
# smoke.sh <env> [max error rate] [duration]: simulated traffic in that environment; exit 1 if too many rides fail.
# The simulator's --max-error-rate turns its summary into an exit code (09.1), and the Job's status into ours.
# The gate between environments (18.6): smoke.sh qa before promoting to prod, smoke.sh prod after.
# The gate keeps the 2m default; a longer run (smoke.sh prod 0.05 5m) fills dashboards whose panels use rate()[5m].
set -euo pipefail
ENV=${1:?usage: smoke.sh <env> [max error rate, default 0.05] [duration, e.g. 90s or 5m, default 2m]}
MAX=${2:-0.05}
DURATION=${3:-2m}
case $DURATION in
  *m) secs=$(( ${DURATION%m} * 60 )) ;;
  *s) secs=${DURATION%s} ;;
  *) echo "duration must end in s or m: $DURATION" >&2; exit 2 ;;
esac
polls=$(( (secs + 180) / 5 ))    # the run itself, plus 3 minutes for the image pull and the pod's start
cd "$(git rev-parse --show-toplevel)"
k() { kubectl --context "$ENV" -n uber-lite "$@"; }

registry=$(terraform -chdir=infra/terraform/ci output -raw registry)
digest=$(aws ecr describe-images --repository-name uber-lite/simulator --output json |
  jq -r '[.imageDetails[] | select(any(.imageTags[]?; startswith("sha-")))] | sort_by(.imagePushedAt) | last | .imageDigest')

# The Job isn't deployed from Git: it's applied on demand, so Argo CD neither sees nor prunes it.
# It talks to the services from inside the cluster (http://rider-api), so QA needs no edge.
k delete job simulator --ignore-not-found --wait
sed -e "s|image: simulator:dev|image: $registry/uber-lite/simulator@$digest|" \
  -e "s|\"--duration\", \"5m\"|\"--duration\", \"$DURATION\", \"--max-error-rate\", \"$MAX\"|" \
  gitops/jobs/simulator/job.yaml | k apply -f -

# Poll instead of `kubectl wait`: waiting for Complete would sit out the whole timeout on a failure.
# backoffLimit: 0 in the Job file, so one failed run fails the Job.
result=""
for _ in $(seq "$polls"); do
  case $(k get job simulator -o jsonpath='{.status.succeeded}/{.status.failed}') in
    1/*) result=passed; break ;;
    */1) result=failed; break ;;
  esac
  sleep 5
done
k logs job/simulator --tail=3 || true
if [ "$result" = passed ]; then
  echo "== smoke test passed on $ENV (ride error rate ≤ $MAX)"
else
  echo "✗ smoke test ${result:-timed out} on $ENV"
  exit 1
fi
