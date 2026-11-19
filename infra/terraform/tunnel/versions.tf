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

provider "cloudflare" {} # reads CLOUDFLARE_API_TOKEN from the environment