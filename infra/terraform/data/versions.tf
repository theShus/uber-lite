terraform {
  required_version = ">= 1.11"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.59" }
    random = { source = "hashicorp/random", version = "~> 3.7" }
  }
  backend "s3" {
    # key          = "data/<env>/terraform.tfstate" - also here cant use key from env, since they are loaded after init
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "uber-lite", layer = "data", env = var.env, owner = "luka", managed-by = "terraform", ttl = "session" }
  }
}
