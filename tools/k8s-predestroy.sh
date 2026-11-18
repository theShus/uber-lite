#!/usr/bin/env bash
# k8s-predestroy.sh: delete everything inside a cluster that owns AWS resources,
# and wait until it's really gone. Run it before `terraform destroy` of the eks layer.
# Usage: tools/k8s-predestroy.sh <cluster name> <kube context>
# Exit:  0 = safe to destroy, 1 = something is still there (read the output)
set -uo pipefail          # no -e: some kubectl calls fail on purpose (a CRD that isn't installed yet)
CLUSTER=${1:?usage: k8s-predestroy.sh <cluster> <context>}
CTX=${2:?usage: k8s-predestroy.sh <cluster> <context>}
REGION=${AWS_REGION:-eu-central-1}                  # $AWS_REGION if set, else eu-central-1
KEEP='^(default|kube-system|kube-public|kube-node-lease|karpenter|external-dns)$'   # namespaces never deleted
k() { kubectl --context "$CTX" "$@"; }              # kubectl, always against this cluster's context

# no cluster: nothing inside it can own AWS resources
if ! aws eks describe-cluster --name "$CLUSTER" --region "$REGION" >/dev/null 2>&1; then
  echo "== $CLUSTER doesn't exist: nothing to clean up inside it"
  exit 0
fi
aws eks update-kubeconfig --name "$CLUSTER" --region "$REGION" --alias "$CTX" >/dev/null   # make sure the context exists

# wait_for <what> <function that prints a count>: poll every 10 s, give up after 10 minutes
wait_for() {
  local what=$1 n="?"
  shift                                             # the rest is the counting function to call
  for _ in $(seq 60); do                            # 60 rounds x 10 s = 10 minutes
    n=$("$@")
    if [ "$n" = "0" ]; then echo "   ✓ $what: none left"; return 0; fi
    echo "   … $what: $n left"
    sleep 10
  done
  echo "   ✗ $what: still $n after 10 minutes"
  return 1
}

# counters for wait_for: each prints one number
# LoadBalancer Services + Ingresses + Gateways, summed by awk (2>/dev/null: the CRD may not exist)
lb_objects() {
  {
    k get svc -A -o json | jq '[.items[] | select(.spec.type == "LoadBalancer")] | length'
    k get ingress -A --no-headers 2>/dev/null | wc -l
    k get gateways.gateway.networking.k8s.io -A --no-headers 2>/dev/null | wc -l
  } | awk '{ s += $1 } END { print s + 0 }'
}
# PVs that should go: everything except Retain. A PV leaves the API only after the CSI driver
# deleted its EBS volume (finalizer), so 0 here means 0 volumes in AWS
deletable_pvs() { k get pv -o json | jq '[.items[] | select(.spec.persistentVolumeReclaimPolicy != "Retain")] | length'; }
# Karpenter's EC2 instances, as NodeClaim objects (0 if Karpenter isn't installed)
nodeclaims() { k get nodeclaims.karpenter.sh --no-headers 2>/dev/null | wc -l; }

# 1. Stop GitOps first, or it recreates everything you delete (topic 18)
if k get namespace argocd >/dev/null 2>&1; then
  echo "== Argo CD: stopping the application controller"
  k -n argocd scale statefulset argocd-application-controller --replicas=0
fi

# 2. Everything that owns a load balancer. The controller removes its finalizer
#    only after AWS has deleted the load balancer, so "object gone" means "LB gone".
echo "== load balancers: deleting LoadBalancer Services, Ingresses, Gateways"
k get svc -A -o json |
  jq -r '.items[] | select(.spec.type == "LoadBalancer") | "\(.metadata.namespace) \(.metadata.name)"' |
  while read -r ns name; do k -n "$ns" delete svc "$name" --wait=false; done   # one "ns name" line per Service
k delete ingress --all -A --wait=false 2>/dev/null
k delete gateways.gateway.networking.k8s.io --all -A --wait=false 2>/dev/null
wait_for "load balancer objects" lb_objects || exit 1    # timeout: exit 1, so make never runs destroy

# 3. Workloads and their volumes: every non-system namespace, plus default's PVCs.
#    A PVC is deleted once no pod uses it; its PV (and EBS volume) goes after it.
echo "== workloads: deleting non-system namespaces"
for ns in $(k get namespaces -o jsonpath='{.items[*].metadata.name}'); do
  [[ $ns =~ $KEEP ]] || k delete namespace "$ns" --wait=false   # delete unless it matches KEEP
done
k -n default delete pvc --all --wait=false
wait_for "persistent volumes (Delete policy)" deletable_pvs || exit 1   # wait for PVs, not namespaces: PVs bill

# 4. Karpenter's nodes, while Karpenter can still terminate them (topic 22)
if k get crd nodepools.karpenter.sh >/dev/null 2>&1; then
  echo "== Karpenter: deleting NodePools"
  k delete nodepools.karpenter.sh --all --wait=false
  wait_for "Karpenter NodeClaims" nodeclaims || exit 1
fi

# 5. Volumes that survive on purpose: list them with their EBS volume IDs (// "?" : default if missing)
retained=$(k get pv -o json |
  jq -r '.items[] | select(.spec.persistentVolumeReclaimPolicy == "Retain") | "\(.metadata.name) \(.spec.csi.volumeHandle // "?")"')
if [ -n "$retained" ]; then
  echo "== ⚠ Retain PVs: their EBS volumes stay after destroy, and keep billing:"
  while read -r line; do echo "   $line"; done <<<"$retained"
fi
echo "== $CLUSTER is safe to destroy"
