TF_DIR := infra/terraform
TF     := terraform

# ---- single-state layers (05.6) -----------------------------------------------
# usage: make tf-plan-network   (read the plan!)   then   make tf-apply-network   make tf-destroy-network
# % is the layer name: tf-plan-network runs in infra/terraform/network
tf-init-%:
	$(TF) -chdir=$(TF_DIR)/$* init -backend-config=../backend.hcl -input=false
tf-plan-%: tf-init-%
	$(TF) -chdir=$(TF_DIR)/$* plan -out=$*.tfplan
tf-apply-%:
	$(TF) -chdir=$(TF_DIR)/$* apply $*.tfplan
tf-destroy-%: tf-init-%
	$(TF) -chdir=$(TF_DIR)/$* destroy

# list everything that bills while idle
check:
	tools/whats-running.sh

.PHONY: check

# ---- environments (15.7) ------------------------------------------------------
# ENV picks the eks state key AND the tfvars file, so the two can never disagree.
# ?= : default, overridden by `make down ENV=qa`.   := : fixed value
ENV     ?= prod
REGION  := eu-central-1
CLUSTER := ul-$(ENV)
EKS_DIR := $(TF_DIR)/eks
DATA_DIR := $(TF_DIR)/data
# the state bucket, read from backend.hcl so its name never lands in Git
BUCKET   := $(shell grep bucket $(TF_DIR)/backend.hcl | cut -d'"' -f2)
ARGOCD_DIR := $(TF_DIR)/argocd

# Auto-detect active spokes: checks AWS to see if the `ul-qa` cluster exists right now.
# If it exists, SPOKES becomes "qa"; if not, it stays empty "".
# Using `?=` means Make only runs this AWS CLI check when a target actually uses $(SPOKES).
SPOKES ?= $(shell aws eks describe-cluster --name ul-qa --region $(REGION) >/dev/null 2>&1 && echo qa)

# stop before anything touches AWS or state if ENV is a typo (ENV=prdo)
guard-env:
	@case "$(ENV)" in qa|prod) ;; *) echo "ENV must be qa or prod, not '$(ENV)'"; exit 1 ;; esac

# 05.6's pattern rules don't know about environments: never use them for eks.
tf-init-eks tf-plan-eks tf-apply-eks tf-destroy-eks tf-init-data tf-plan-data tf-apply-data tf-destroy-data:
	@echo "eks and data have one state per environment: use 'make up/down ENV=…' or the eks-/data- targets"; exit 1

# runs before every eks target: point the folder at THIS env's state (-reconfigure),
# and rewrite terraform.tfvars with today's home IP
eks-init: guard-env
	$(TF) -chdir=$(EKS_DIR) init -input=false -reconfigure \
	  -backend-config=../backend.hcl -backend-config=key=eks/$(ENV)/terraform.tfstate
	tools/write-eks-tfvars.sh $(EKS_DIR)/terraform.tfvars

data-init: guard-env
	$(TF) -chdir=$(DATA_DIR) init -input=false -reconfigure \
	  -backend-config=../backend.hcl -backend-config=key=data/$(ENV)/terraform.tfstate

# RDS + the services' secrets (17.3); built next to eks-up, not after it
data-up: data-init
	tools/tf-apply-safe.sh $(DATA_DIR) -var-file=$(ENV).tfvars -var state_bucket=$(BUCKET)

data-down: data-init
	$(TF) -chdir=$(DATA_DIR) destroy -input=false -auto-approve -var-file=$(ENV).tfvars -var state_bucket=$(BUCKET)

# ---- up ----------------------------------------------------------------------
# network with fck-nat; tf-apply-safe refuses if the plan deletes anything
network-up: tf-init-network
	tools/tf-apply-safe.sh $(TF_DIR)/network -var nat_mode=instance

# the cluster for ENV, then a kube context named after ENV (prod / qa)
eks-up: eks-init
	tools/tf-apply-safe.sh $(EKS_DIR) -var-file=$(ENV).tfvars
	aws eks update-kubeconfig --name $(CLUSTER) --region $(REGION) --alias $(ENV)

# the whole chain, timed. $(MAKE) runs a sub-make, and ENV is passed down to it.
# Everything inside the cluster is Argo CD's job (18.5): the certificate goes back first (a key can't live in Git),
# then argocd-up installs Argo CD and the root, and waits until every Application is Synced and Healthy.
# Build an environment: network -> database + EKS cluster -> TLS -> register in prod's Argo CD
up: guard-env
	@# Safety check 1: QA cannot exist without prod, because prod holds Argo CD and the `ul-prod-argocd` IAM role
	@if [ "$(ENV)" = qa ] && ! aws eks describe-cluster --name ul-prod --region $(REGION) >/dev/null 2>&1; then \
	  echo "✗ QA is a spoke of prod's Argo CD: 'make up ENV=prod' first (18.6)"; exit 1; \
	fi
	@date '+== up $(ENV): start %T'
	$(MAKE) network-up
	@echo "== data and eks in parallel: each one's output appears when it finishes (~15 min)"
	$(MAKE) -j2 --output-sync=target data-up eks-up
	tools/tls-restore.sh $(ENV)
	@# Works for BOTH prod and qa: once `ul-qa` is created above, $(SPOKES) automatically resolves to "qa"
	$(MAKE) argocd-up
	@date '+== up $(ENV): done %T'

# ---- Argo CD on prod, the hub (18.5) ---------------------------------------------
# argocd-init has no -reconfigure: this layer has one state, so the folder never switches keys (unlike eks and data)
argocd-init:
	$(TF) -chdir=$(ARGOCD_DIR) init -input=false -backend-config=../backend.hcl
	@printf 'state_bucket = "%s"\nrepo_url     = "%s"\n' \
	  "$$(grep bucket $(TF_DIR)/backend.hcl | cut -d'"' -f2)" \
	  "$$(yq '.repoURL' gitops/bootstrap/root/values.yaml)" > $(ARGOCD_DIR)/terraform.tfvars

# Installs or updates Argo CD on prod, registering whatever spokes currently exist in AWS
argocd-up: argocd-init
	tools/tf-apply-safe.sh $(ARGOCD_DIR) -var spokes=$(SPOKES)
	tools/argocd-wait.sh prod

# Used when tearing down QA (`make down ENV=qa`): we DO NOT uninstall Argo CD (prod still needs it!),
# we just re-apply the layer with `spokes=` (empty) so Argo CD deletes QA's cluster Secret and `root-qa`.
argocd-deregister: argocd-init
	@if aws eks describe-cluster --name ul-prod --region $(REGION) >/dev/null 2>&1; then \
	  tools/tf-apply-safe.sh $(ARGOCD_DIR) -var spokes=; \
	else \
	  echo "== ul-prod doesn't exist: no hub to deregister from"; \
	fi

# Used ONLY when tearing down prod (`make down ENV=prod`): completely uninstalls Argo CD
argocd-down: argocd-init
	@if aws eks describe-cluster --name ul-prod --region $(REGION) >/dev/null 2>&1; then \
	  $(TF) -chdir=$(ARGOCD_DIR) destroy -input=false -auto-approve; \
	else \
	  echo "== ul-prod doesn't exist: no Argo CD to uninstall"; \
	fi

# ---- down --------------------------------------------------------------------
# 1. delete what controllers created in AWS (LBs, volumes, Karpenter nodes) and wait;
#    if the script fails, make stops HERE and the cluster is NOT destroyed
# 2. destroy the cluster   3. drop the kube context (- : ignore failure if it's already gone)
# Argo CD goes before the predestroy script: its self-heal would recreate what the script deletes (18.5)
# Tear down the EKS cluster: on prod, uninstall Argo CD; on qa, just remove qa from prod's Argo CD
eks-down: eks-init
	tools/tls-backup.sh $(ENV)
	@if [ "$(ENV)" = prod ]; then $(MAKE) argocd-down; else $(MAKE) argocd-deregister; fi
	tools/k8s-predestroy.sh $(CLUSTER) $(ENV)
	$(TF) -chdir=$(EKS_DIR) destroy -input=false -auto-approve -var-file=$(ENV).tfvars
	-kubectl config delete-context $(ENV)

# destroy the network only if no other cluster still runs in it ($$ : a literal $ for the shell)
# network-down: keep the network while a cluster OR a database still uses it
network-down: tf-init-network
	@if [ -n "$$(aws eks list-clusters --region $(REGION) --query 'clusters[]' --output text)" ] || \
	    [ -n "$$(aws rds describe-db-instances --region $(REGION) --query 'DBInstances[].DBInstanceIdentifier' --output text)" ]; then \
	  echo "== keeping the network: a cluster or a database still uses it"; \
	else \
	  $(TF) -chdir=$(TF_DIR)/network destroy -input=false -auto-approve; \
	fi


# Full teardown: EKS + database in parallel -> shared network (skipped if another env still uses it)
down: guard-env
	@# Safety check 2: never destroy prod while qa is still running, or qa is left orphaned without Argo CD
	@if [ "$(ENV)" = prod ] && aws eks describe-cluster --name ul-qa --region $(REGION) >/dev/null 2>&1; then \
	  echo "✗ ul-qa still runs: 'make down ENV=qa' first. A spoke goes before its hub (18.6)"; exit 1; \
	fi
	@date '+== down $(ENV): start %T'
	@echo "== eks and data in parallel: each one's output appears when it finishes (~15 min)"
	$(MAKE) -j2 --output-sync=target eks-down data-down
	$(MAKE) network-down
	tools/whats-running.sh $(REGION) || echo "!! something is still billing: read the ✗ lines above"
	@date '+== down $(ENV): done %T'

# these targets are commands, not files: always run them
.PHONY: guard-env eks-init network-up eks-up up eks-down network-down down data-init data-up data-down argocd-init argocd-up argocd-deregister argocd-down
