terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }

  backend "s3" {
    key          = "k8s-lab/terraform.tfstate"
    use_lockfile = true
  }
}

provider "aws" {
  region = "eu-central-1"

  default_tags {
    tags = {
      project    = "uber-lite"
      layer      = "k8s-lab"
      owner      = "luka"
      managed-by = "terraform"
      ttl        = "session"
    }
  }
}
