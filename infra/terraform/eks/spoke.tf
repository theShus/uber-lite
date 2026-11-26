# HUB AND SPOKE SETUP (18.6)
# Instead of installing a separate Argo CD on every cluster, we run ONE Argo CD on prod (the "hub")
# and connect qa (a "spoke") to it so prod's Argo CD deploys to both clusters.
#
# For prod's Argo CD to control qa, we need 3 things ("ID Badge, Guest List, and Open Door"):
# 1. ID Badge (on prod): Give Argo CD's pods an AWS IAM role so they can prove who they are.
# 2. Guest List (on qa): Tell qa's EKS cluster to trust that (prod's) IAM role as an admin.
# 3. Open Door (on qa): Open port 443 on qa's firewall so prod's pods can actually reach qa's API.


# 1. THE ID BADGE (runs ONLY on prod)
# Creates an AWS IAM role named "ul-prod-argocd" and hands it to Argo CD's 3 pods via Pod Identity.
# Why does this role have NO AWS permissions/policies attached?
# Because it doesn't need to create AWS resources (like S3 or EC2). It only needs to exist so Argo CD can sign an EKS login token
module "argocd_pod_identity" {
  source  = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  create             = var.env == "prod" # Only the hub (prod) runs Argo CD, so qa skips this
  name               = "${local.name}-argocd"
  use_name_prefix    = false
  policy_name_prefix = "${local.name}-"

  associations = {
    for sa in ["argocd-application-controller", "argocd-applicationset-controller", "argocd-server"] : sa => {
      cluster_name    = module.eks.cluster_name
      namespace       = "argocd"
      service_account = sa
    }
  }
}


# Prod's Argo CD pods live inside these subnets, and qa's firewall needs their IPs.
data "aws_subnet" "private" {
  for_each = toset(local.net.private_subnet_ids)
  id       = each.value
}


#both of these locals are activated in main, the var.env just gives it either an empty or full data map, based on env
locals {
  is_spoke = var.env != "prod"

  # 2. THE GUEST LIST (qa / spokes)
  # Tells this spoke cluster: If prod's Argo CD shows up with the ul-prod-argocd role, let it in as cluster admin
  # ! prod MUST be built before qa
  hub_access_entries = local.is_spoke ? {
    argocd-hub = {
      principal_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/ul-prod-argocd"
      policy_associations = {
        admin = {
          policy_arn   = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy" # to the ul-prod-argocd role (created above) we attach eks admin policy
          access_scope = { type = "cluster" }
        }
      }
    }
  } : {}

  # 3. THE OPEN DOOR (qa / spokes)
  # Qa's Kubernetes API is private (inside our VPC). By default, its firewall blocks traffic from outside qa.
  # This opens port 443 (HTTPS) for traffic coming from the private subnets where prod's Argo CD pods run.
  # Without this rule, Argo CD's connection to qa just hangs and times out.
  hub_api_rules = local.is_spoke ? {
    argocd_hub = {
      description = "Argo CD on the hub (prod pods) to this API server"
      protocol    = "tcp"
      from_port   = 443
      to_port     = 443
      type        = "ingress"
      cidr_blocks = [for s in data.aws_subnet.private : s.cidr_block] # open ingress on 443 from the prod priv subnets
    }
  } : {}
}
