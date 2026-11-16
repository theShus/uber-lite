#!/usr/bin/env bash
# write-eks-tfvars.sh: (re)write the eks layer's gitignored terraform.tfvars with today's values.
# Your home IP can change between sessions, and an old one locks kubectl out (15.6 §3).
# Usage: tools/write-eks-tfvars.sh infra/terraform/eks/terraform.tfvars
set -euo pipefail                                  # stop on any error, unset variable, or failed pipe stage
out=${1:?usage: write-eks-tfvars.sh <path to terraform.tfvars>}   # first argument, or stop with this message
backend="$(dirname "$out")/../backend.hcl"         # infra/terraform/backend.hcl, next to the layer folders

bucket=$(grep bucket "$backend" | cut -d'"' -f2)   # the value between the first pair of quotes
ip=$(curl -4 -fsS https://checkip.amazonaws.com)   # today's public IPv4 (-f: fail on HTTP errors)
arn=$(aws sts get-caller-identity --query Arn --output text)   # who is running this
case $arn in                                       # access entries refuse STS session ARNs
  arn:aws:sts::*) echo "✗ $arn is a session ARN; access entries need the IAM role's ARN (15.6 §3)"; exit 1 ;;
esac

# overwrite the file every run: it's gitignored, and always rebuilt from live values
cat > "$out" <<TFVARS
state_bucket        = "$bucket"
public_access_cidrs = ["$ip/32"]
cluster_admins      = { luka = "$arn" }
TFVARS
echo "== wrote $out (home IP $ip)"
