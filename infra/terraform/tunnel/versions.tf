terraform {
  required_version = ">= 1.11"
  required_providers {
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 5.8" }
  }
  backend "s3" {
    key          = "tunnel/terraform.tfstate"
    use_lockfile = true
  }
}

provider "cloudflare" {
    api_token = var.cloudflare_api_token
} # should be read from the env or some secret manager, but ive ploped it into tfvars, am lazy