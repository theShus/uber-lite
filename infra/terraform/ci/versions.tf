terraform {
  required_version = ">= 1.11"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  backend "s3" {
    key          = "ci/terraform.tfstate"
    use_lockfile = true
  }
}

provider "aws" {
  region = "eu-central-1"
  default_tags {
    tags = { project = "uber-lite", layer = "ci", owner = "luka", managed-by = "terraform" }
  }
}
