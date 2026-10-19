TF_DIR := infra/terraform
TF     := terraform

# usage: make tf-plan-network   (read the plan!)   then   make tf-apply-network   make tf-destroy-network
tf-init-%:
	$(TF) -chdir=$(TF_DIR)/$* init -backend-config=../backend.hcl -input=false
tf-plan-%: tf-init-%
	$(TF) -chdir=$(TF_DIR)/$* plan -out=$*.tfplan
tf-apply-%:
	$(TF) -chdir=$(TF_DIR)/$* apply $*.tfplan
tf-destroy-%: tf-init-%
	$(TF) -chdir=$(TF_DIR)/$* destroy

check:
	tools/whats-running.sh

.PHONY: check
