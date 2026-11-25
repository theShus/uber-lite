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
up: guard-env
	@date '+== up $(ENV): start %T'
	$(MAKE) network-up
	$(MAKE) -j2 --output-sync=line eks-up data-up
	tools/tls-restore.sh $(ENV)
	@if [ "$(ENV)" = prod ]; then $(MAKE) argocd-up; else echo "== $(ENV) has no Argo CD of its own: 18.6 makes it a spoke of prod's"; fi
	@date '+== up $(ENV): done %T'

# ---- Argo CD on prod, the hub (18.5) ---------------------------------------------
# argocd-init has no -reconfigure: this layer has one state, so the folder never switches keys (unlike eks and data)
argocd-init:
	$(TF) -chdir=$(ARGOCD_DIR) init -input=false -backend-config=../backend.hcl
	@printf 'state_bucket = "%s"\nrepo_url     = "%s"\n' \
	  "$$(grep bucket $(TF_DIR)/backend.hcl | cut -d'"' -f2)" \
	  "$$(yq '.repoURL' gitops/bootstrap/root/values.yaml)" > $(ARGOCD_DIR)/terraform.tfvars

argocd-up: argocd-init
	tools/tf-apply-safe.sh $(ARGOCD_DIR)
	tools/argocd-wait.sh prod

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
eks-down: eks-init
	tools/tls-backup.sh $(ENV)
	@if [ "$(ENV)" = prod ]; then $(MAKE) argocd-down; fi
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


# the whole chain, timed, ending with the billing check
down: guard-env
	@date '+== down $(ENV): start %T'
	$(MAKE) -j2 --output-sync=line eks-down data-down
	$(MAKE) network-down
	tools/whats-running.sh $(REGION) || echo "!! something is still billing: read the ✗ lines above"
	@date '+== down $(ENV): done %T'

# these targets are commands, not files: always run them
.PHONY: guard-env eks-init network-up eks-up up eks-down network-down down data-init data-up data-down argocd-init argocd-up argocd-down
