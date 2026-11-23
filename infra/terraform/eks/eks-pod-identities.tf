# this creates roles for LBC, poiicy -> role -> SA association. So the LBC pod can operate.

# we need to get DNS zone_id and NS from the dns state outputs
data "terraform_remote_state" "dns" {
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "dns/terraform.tfstate", region = var.region }
}

data "aws_caller_identity" "current" {}

locals {
  zone_id  = data.terraform_remote_state.dns.outputs.zone_id
  zone_arn = "arn:aws:route53:::hostedzone/${local.zone_id}"

  # for ESO for secret manger
  arn_prefix = "${var.region}:${data.aws_caller_identity.current.account_id}"
}


module "lbc_pod_identity" {
  source  = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  name                            = "${local.name}-lbc"
  use_name_prefix                 = false
  policy_name_prefix              = "${local.name}-"
  attach_aws_lb_controller_policy = true

  associations = {
    this = {
      cluster_name    = "${module.eks.cluster_name}"
      namespace       = "kube-system"
      service_account = "aws-load-balancer-controller"
    }
  }
}

module "cert_manager_pod_identity" {
  source  = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  name                          = "${local.name}-cert-manager"
  use_name_prefix               = false
  policy_name_prefix            = "${local.name}-"
  attach_cert_manager_policy    = true
  cert_manager_hosted_zone_arns = [local.zone_arn]

  associations = {
    this = {
      cluster_name    = "${module.eks.cluster_name}"
      namespace       = "cert-manager"
      service_account = "cert-manager"
    }
  }
}

module "external_dns_pod_identity" {
  source  = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  name                          = "${local.name}-external-dns"
  use_name_prefix               = false
  policy_name_prefix            = "${local.name}-"
  attach_external_dns_policy    = true
  external_dns_hosted_zone_arns = [local.zone_arn]

  associations = {
    this = {
      cluster_name    = "${module.eks.cluster_name}"
      namespace       = "external-dns"
      service_account = "external-dns"
    }
  }
}


module "external_secrets_pod_identity" {
  source  = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  name                           = "${local.name}-external-secrets"
  use_name_prefix                = false
  policy_name_prefix             = "${local.name}-"
  attach_external_secrets_policy = true
  external_secrets_secrets_manager_arns = [
    "arn:aws:secretsmanager:${local.arn_prefix}:secret:uber-lite/${var.env}/*",
    "arn:aws:secretsmanager:${local.arn_prefix}:secret:rds!db-*", # this a pattern and not a direct arn load reference from data, because that would make EKS terra dependent on Data terra, which we do not want.
  ] 
  external_secrets_ssm_parameter_arns = [
    "arn:aws:ssm:${local.arn_prefix}:parameter/uber-lite/${var.env}/*",
  ]

  associations = {
    this = {
      cluster_name    = module.eks.cluster_name
      namespace       = "external-secrets"
      service_account = "external-secrets"
    }
  }
}
