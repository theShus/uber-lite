# this creates roles for LBC, poiicy -> role -> SA association. So the LBC pod can operate.

# we need to get DNS zone_id and NS from the dns state outputs
data "terraform_remote_state" "dns" {
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "dns/terraform.tfstate", region = var.region }
}

locals {
  zone_id = data.terraform_remote_state.dns.outputs.zone_id
  zone_arn = "arn:aws:route53:::hostedzone/${local.zone_id}"
}

module "lbc_pod_identity" {
  source = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  name = "${local.name}-lbc"
  use_name_prefix = false
  policy_name_prefix = "${local.name}-"
  attach_aws_lb_controller_policy = true

  associations = {
    this = {
      cluster_name = "${module.eks.cluster_name}"
      namespace = "kube-system"
      service_account = "aws-load-balancer-controller"
    }
  }
}

module "cert_manager_pod_identity" {
  source = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  name = "${local.name}-cert-manager"
  use_name_prefix = false
  policy_name_prefix = "${local.name}-"
  attach_cert_manager_policy = true 
  cert_manager_hosted_zone_arns = [local.zone_arn]

  associations = {
    this = {
      cluster_name = "${module.eks.cluster_name}"
      namespace = "cert-manager"
      service_account = "cert-manager"
    }
  }
}

module "external_dns_pod_identity" {
  source = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  name = "${local.name}-external-dns"
  use_name_prefix = false
  policy_name_prefix = "${local.name}-"
  attach_external_dns_policy = true
  external_dns_hosted_zone_arns = [local.zone_arn]

  associations = {
    this = {
      cluster_name = "${module.eks.cluster_name}"
      namespace = "external-dns"
      service_account = "external-dns"
    }
  }
}
