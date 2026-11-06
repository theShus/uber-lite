#!/usr/bin/env bash
# dev-images.sh [service...]: build dev images and load them into kind (all services by default).
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if [ $# -gt 0 ]; then
  services=("$@")
else
  services=($(ls apps))
fi

for svc in "${services[@]}"; do
  echo "== $svc"
  docker build -q -t "$svc:dev" "apps/$svc" >/dev/null
  kind load docker-image "$svc:dev" --name dev >/dev/null
  echo "$svc:dev built and loaded"
done
