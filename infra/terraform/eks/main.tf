# bucket we are getting outputs from, aka priv subnet IDs
data "terraform_remote_state" "network" {
  backend = "s3"
  config = {
    bucket = var.state_bucket,
    key = "network/terraform.tfstate",
    region = var.region
  }
}


locals {
    name = "ul-${var.env}" # names of our qa and prod EKS
    net = data.terraform_remote_state.network.outputs # all network output varaibles from the data state we declared above
}


# Svesni kompromisi laba, za Trivy (CI). Ističu na kraju track-a, pa se odluka mora ponoviti.
# trivy:ignore:AWS-0040:exp:2027-01-31 -- public endpoint, limited to the home IP; production would be private-only
# trivy:ignore:AWS-0039:exp:2027-01-31 -- no customer KMS key; EKS still envelope-encrypts Secrets with an AWS-owned key
# trivy:ignore:AWS-0104:exp:2027-01-31 -- nodes need egress through NAT; per-workload egress is limited by NetworkPolicies
module "eks" {
    
}
