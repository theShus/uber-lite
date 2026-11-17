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

# stop before anything touches AWS or state if ENV is a typo (ENV=prdo)
guard-env:
	@case "$(ENV)" in qa|prod) ;; *) echo "ENV must be qa or prod, not '$(ENV)'"; exit 1 ;; esac

# 05.6's pattern rules don't know about environments: never use them for eks.
tf-init-eks tf-plan-eks tf-apply-eks tf-destroy-eks:
	@echo "The eks layer has one state per environment: use 'make eks-up ENV=…' or 'make eks-down ENV=…'"; exit 1

# runs before every eks target: point the folder at THIS env's state (-reconfigure),
# and rewrite terraform.tfvars with today's home IP
eks-init: guard-env
	$(TF) -chdir=$(EKS_DIR) init -input=false -reconfigure \
	  -backend-config=../backend.hcl -backend-config=key=eks/$(ENV)/terraform.tfstate
	tools/write-eks-tfvars.sh $(EKS_DIR)/terraform.tfvars

# ---- up ----------------------------------------------------------------------
# network with fck-nat; tf-apply-safe refuses if the plan deletes anything
network-up: tf-init-network
	tools/tf-apply-safe.sh $(TF_DIR)/network -var nat_mode=instance

# the cluster for ENV, then a kube context named after ENV (prod / qa)
eks-up: eks-init
	tools/tf-apply-safe.sh $(EKS_DIR) -var-file=$(ENV).tfvars
	aws eks update-kubeconfig --name $(CLUSTER) --region $(REGION) --alias $(ENV)

# what lives inside the cluster: today only the gp3 StorageClass (topic 18: Argo CD's bootstrap)
platform-up: guard-env
	kubectl --context $(ENV) wait --for=condition=Ready nodes --all --timeout=5m
	tools/lbc-platform-up.sh $(ENV)

# the whole chain, timed. $(MAKE) runs a sub-make, and ENV is passed down to it
up: guard-env
	@date '+== up $(ENV): start %T'
	$(MAKE) network-up
	$(MAKE) eks-up
	$(MAKE) platform-up
	@date '+== up $(ENV): done %T'

# ---- down --------------------------------------------------------------------
# 1. delete what controllers created in AWS (LBs, volumes, Karpenter nodes) and wait;
#    if the script fails, make stops HERE and the cluster is NOT destroyed
# 2. destroy the cluster   3. drop the kube context (- : ignore failure if it's already gone)
eks-down: eks-init
	tools/k8s-predestroy.sh $(CLUSTER) $(ENV)
	$(TF) -chdir=$(EKS_DIR) destroy -input=false -auto-approve -var-file=$(ENV).tfvars
	-kubectl config delete-context $(ENV)

# destroy the network only if no other cluster still runs in it ($$ : a literal $ for the shell)
network-down: tf-init-network
	@if [ -n "$$(aws eks list-clusters --region $(REGION) --query 'clusters[]' --output text)" ]; then \
	  echo "== keeping the network: another cluster still uses it"; \
	else \
	  $(TF) -chdir=$(TF_DIR)/network destroy -input=false -auto-approve; \
	fi

# the whole chain, timed, ending with the billing check
down: guard-env
	@date '+== down $(ENV): start %T'
	$(MAKE) eks-down
	$(MAKE) network-down
	tools/whats-running.sh $(REGION) || echo "!! something is still billing: read the ✗ lines above"
	@date '+== down $(ENV): done %T'

# these targets are commands, not files: always run them
.PHONY: guard-env eks-init network-up eks-up platform-up up eks-down network-down down
