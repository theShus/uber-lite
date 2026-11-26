#!/usr/bin/env bash
# promote.sh <qa|prod> [service ...]: open a pull request that changes which image digests an environment runs.
#   qa    the newest sha- build of each service in ECR (what CI built from main)
#   prod  exactly the digests QA runs now: the bytes QA tested, nothing rebuilt (13.4)
# Default: all services. Needs yq, gh (logged in) and, for qa, AWS credentials. Merging the PR is the deploy (18.6).
set -euo pipefail
ENV=${1:?usage: promote.sh <qa|prod> [service ...]}
shift
cd "$(git rev-parse --show-toplevel)"
case $ENV in qa | prod) ;; *) echo "✗ promote to qa or prod (dev runs local builds)"; exit 1 ;; esac
services=("$@")
[ ${#services[@]} -gt 0 ] || mapfile -t services < <(ls gitops/apps)

newest_in_ecr() { # the newest image of uber-lite/$1 that carries a sha- tag
  aws ecr describe-images --repository-name "uber-lite/$1" --output json |
    jq -r '[.imageDetails[] | select(any(.imageTags[]?; startswith("sha-")))] | sort_by(.imagePushedAt) | last | .imageDigest // empty'
}

# A promotion branch starts from origin/main, not from whatever is checked out: it contains only the promotion.
git fetch -q origin main
branch="promote/$ENV-$(date +%Y%m%d-%H%M%S)"
git switch -q -c "$branch" origin/main

changed=()
for svc in "${services[@]}"; do
  file="gitops/apps/$svc/values-$ENV.yaml"
  if [ "$ENV" = qa ]; then
    digest=$(newest_in_ecr "$svc")
  else
    digest=$(yq '.image.digest // ""' "gitops/apps/$svc/values-qa.yaml") # prod gets QA's tested digest
  fi
  [ -n "$digest" ] || { echo "✗ $svc: no digest to promote"; exit 1; }
  current=$(yq '.image.digest // ""' "$file")
  if [ "$digest" != "$current" ]; then
    DIGEST=$digest yq -i '.image.digest = strenv(DIGEST)' "$file" # strenv: the value is never parsed as yq syntax
    changed+=("$svc ${current:7:12} → ${digest:7:12}")
  fi
done

if [ ${#changed[@]} -eq 0 ]; then
  echo "== $ENV already runs these digests: nothing to promote"
  git switch -q - && git branch -q -D "$branch"
  exit 0
fi
body=$(printf -- '- %s\n' "${changed[@]}")
git commit -q -am "Promote to $ENV: ${#changed[@]} service(s)"
git push -q -u origin "$branch"
gh pr create --base main --head "$branch" --title "Promote to $ENV: ${#changed[@]} service(s)" --body "$body"
git switch -q - # back to the branch you were on
