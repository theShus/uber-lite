terraform {
  required_version = ">= 1.11"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.59" }
  }
  backend "s3" {
    # key          = "eks/terraform.tfstate"
    # we are not using this because we want to separate the QA and PROD env state files, this way they would use the same tfstate
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "uber-lite", layer = "eks", env = var.env, owner = "luka", managed-by = "terraform", ttl = "session" }
  }
}
