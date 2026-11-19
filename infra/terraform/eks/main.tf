# bucket we are getting outputs from, aka priv subnet IDs
data "terraform_remote_state" "network" {
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "network/terraform.tfstate", region = var.region }
}

locals {
  name = "ul-${var.env}"                             # names of our qa and prod EKS
  net  = data.terraform_remote_state.network.outputs # all network output varaibles from the data state we declared above
}


# Lab trade-offs, reviewed like code. Each expires at the end of the track.
# trivy:ignore:AWS-0040:exp:2027-01-31 -- public endpoint, limited to the home IP; production would be private-only
# trivy:ignore:AWS-0104:exp:2027-01-31 -- nodes need egress (ECR, STS, EKS) through NAT; per-workload egress is limited by NetworkPolicies
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.29.0"

  name               = local.name
  kubernetes_version = var.kubernetes_version
  upgrade_policy     = { support_type = "STANDARD" } # never drift into extended support

  vpc_id     = local.net.vpc_id
  subnet_ids = local.net.private_subnet_ids # control-plane ENIs and nodes

  endpoint_private_access      = true                    # nodes reach the API inside the VPC
  endpoint_public_access       = true                    # you, from the hub...
  endpoint_public_access_cidrs = var.public_access_cidrs # ...and only from home

  # Who may use the cluster: explicit access entries, nothing implicit
  authentication_mode                      = "API"
  enable_cluster_creator_admin_permissions = false
  access_entries = {
    for name, arn in var.cluster_admins : name => {
      principal_arn = arn
      policy_associations = {
        admin = {
          policy_arn   = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = { type = "cluster" }
        }
      }
    }
  }

  # Secrets encrypted with our own KMS key (module default: create_kms_key = true,
  # encryption_config = { resources = ["secrets"] }); only the deletion wait is shortened
  kms_key_deletion_window_in_days = 7 # minimum; the default is 30

  # Cost-safe overrides of the module's defaults
  enabled_log_types           = []    # default: audit, api, authenticator
  create_cloudwatch_log_group = false # default: a log group with 90-day retention
  enable_irsa                 = false # Pod Identity instead, so no OIDC provider

  addons = {
    vpc-cni = {
      before_compute = true # nodes must find the CNI configured when they join
      configuration_values = jsonencode({
        enableNetworkPolicy = "true" # NetworkPolicies are enforced, like on dev
        env = {
          ENABLE_PREFIX_DELEGATION = "true"
          WARM_PREFIX_TARGET       = "1"
        }
      })
    }
    eks-pod-identity-agent = { before_compute = true }
    kube-proxy             = {}
    coredns                = {}
    metrics-server         = {}
    aws-ebs-csi-driver = { # ebs container storage interface, this is also a Pod, we give it a SA that has a ebs create role attached to it
      pod_identity_association = [{
        role_arn        = module.ebs_csi_pod_identity.iam_role_arn
        service_account = "ebs-csi-controller-sa" # we make a SA that has the role that can create ebs block for discs
      }]
    }
  }

  eks_managed_node_groups = {
    system = {
      ami_type       = "AL2023_ARM_64_STANDARD"
      instance_types = var.node_instance_types
      capacity_type  = var.node_capacity_type
      min_size       = 2
      max_size       = 3
      desired_size   = 2

      block_device_mappings = { # hard disc that we are attaching to each node
        root = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size           = 20
            volume_type           = "gp3" # general purpose ssd, just saying its an SSD type
            encrypted             = true
            delete_on_termination = true
          }
        }
      }

      # This is for setting up how we contact IMDS.
      # IMDS is a aws managed Meta Data Service that gives aws objects metadata, network data, IPs, privs, who they are ...
      # It is supposed to give Node Workers strong privs and metadata because Nodes need stronger privs to operate

      # However if we do not make a Pod Identity before starting a pod, it will try to also contact IMDS to get key in order to perate, the issue here is that
      # it will get Node privs instead of limited pod privs, so it will have too much power.

      # How do we solve this, Node needs to hop throught a virtual ethernet cable to get to IMDS, that is 1 hop
      # Pod needs 2 hops, to server and then to IMDS, so if we say that IMDS will only accept 1 hops attempts, pods can never get node privs

      metadata_options = {
        http_endpoint               = "enabled"  # enable IMDS endpoint access, so we can access it in general
        http_tokens                 = "required" # IMDSv2 only, newer and stronger version of the service
        http_put_response_hop_limit = 1          # pods can't reach IMDS, locked to 1 hop so only nodes can
      }

      labels = { pool = "system" }
    }
  }
}

# Creates a IAM role that has privs to create EBS and to attach them to node workers, we attach this to CSI in addons in EKS block
module "ebs_csi_pod_identity" {
  source  = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  name                      = "${local.name}-ebs-csi"
  use_name_prefix           = false
  policy_name_prefix        = "${local.name}-" # IAM policy names are account-wide: ul-prod-EBS_CSI, ul-qa-EBS_CSI
  attach_aws_ebs_csi_policy = true
  # No associations here: the aws-ebs-csi-driver add-on above creates its own.
}

# Control plane -> admission webhooks on the nodes. The module opens 443/4443/6443/8443/9443/10250;
# Kong's KIC listens on 8080, and without this every Secret/Service write waits 10 s for it (16.3).
node_security_group_additional_rules = {
  ingress_cluster_8080_kong_webhook = {
    description                   = "Cluster API to Kong admission webhook 8080/tcp"
    protocol                      = "tcp"
    from_port                     = 8080
    to_port                       = 8080
    type                          = "ingress"
    source_cluster_security_group = true
  }
}
