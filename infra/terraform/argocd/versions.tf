terraform {
  required_version = ">= 1.11"
  required_providers {
    aws  = { source = "hashicorp/aws", version = "~> 6.59" }
    helm = { source = "hashicorp/helm", version = "~> 3.3" }
  }
  backend "s3" {
    key          = "argocd/prod/terraform.tfstate" # one Argo CD, on prod: the hub (18.6 adds spokes to it)
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "uber-lite", layer = "argocd", env = "prod", owner = "luka", managed-by = "terraform", ttl = "session" }
  }
}

# Helm talks to the cluster with a short-lived token from `aws eks get-token`, like kubectl (15.2).
provider "helm" {
  kubernetes = {
    host                   = data.aws_eks_cluster.hub.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.hub.certificate_authority[0].data)
    exec = {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", data.aws_eks_cluster.hub.name, "--region", var.region]
    }
  }
}
