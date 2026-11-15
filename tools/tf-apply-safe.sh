#!/usr/bin/env bash
# tf-apply-safe.sh: plan, refuse if the plan deletes or replaces anything, then apply exactly that plan.
# Usage: tools/tf-apply-safe.sh <layer dir> [extra plan arguments...]
# A build from zero only creates. A delete during "make up" is a surprise, and a human reads it first.
set -euo pipefail
dir=${1:?usage: tf-apply-safe.sh <layer dir> [plan args...]}
shift                                              # drop $1, so "$@" is only the extra plan arguments

# save the plan to a file: what gets checked is exactly what gets applied
terraform -chdir="$dir" plan -input=false -out=up.tfplan "$@"

# count resources whose actions contain "delete": a plain delete, or a replace
# (["delete","create"] / ["create","delete"]). []? : no error if the list is missing
deletes=$(terraform -chdir="$dir" show -json up.tfplan |
  jq '[.resource_changes[]? | select(.change.actions | index("delete"))] | length')
if [ "$deletes" -ne 0 ]; then
  echo "✗ REFUSING to apply: this plan deletes or replaces $deletes resource(s)."
  echo "  Read it with: terraform -chdir=$dir show up.tfplan"
  exit 1
fi

# no deletes: apply the saved plan (no "yes" prompt for a saved plan)
terraform -chdir="$dir" apply -input=false up.tfplan
