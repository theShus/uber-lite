#!/usr/bin/env bash
# whats-running.sh: list AWS resources that bill while idle.
# Usage:  tools/whats-running.sh [region ...]    (default: eu-central-1 eu-north-1 us-east-1)
# Exit:   0 = nothing found, 1 = something is running (or a check failed)
set -uo pipefail

if [ $# -gt 0 ]; then regions=("$@"); else regions=(eu-central-1 eu-north-1 us-east-1); fi
found=0

# check LABEL COMMAND... : run an AWS CLI command and print a line only if it returned something
check() {
  local label=$1; shift
  local out
  if ! out=$("$@" 2>&1); then
    echo "  ?  $label: check failed: ${out%%$'\n'*}"
    found=1; return
  fi
  out=$(tr -s '[:space:]' ' ' <<<"$out"); out=${out# }; out=${out% }
  if [ -n "$out" ] && [ "$out" != "None" ]; then
    echo "  ✗  $label: $out"
    found=1
  fi
}

for r in "${regions[@]}"; do
  echo "== $r"
  q=(--region "$r" --output text)
  check "EKS clusters"  aws eks list-clusters "${q[@]}" --query 'clusters'
  check "EC2 instances" aws ec2 describe-instances "${q[@]}" \
          --filters Name=instance-state-name,Values=pending,running,stopping,stopped \
          --query 'Reservations[].Instances[].[InstanceId,InstanceType]'
  check "NAT gateways"  aws ec2 describe-nat-gateways "${q[@]}" \
          --filter Name=state,Values=pending,available --query 'NatGateways[].NatGatewayId'
done

if [ "$found" -eq 0 ]; then echo "✅ Nothing billing found."; else echo "❌ Something is still running (see ✗ / ? lines)."; fi
exit "$found"
