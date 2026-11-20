terraform {
  required_version = ">= 1.11"
  required_providers {
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 5.8" }
    aws        = { source = "hashicorp/aws", version = "~> 6.59" } # the token's copy in Parameter Store (17.5)
  }

  backend "s3" {
    key          = "tunnel/terraform.tfstate"
    use_lockfile = true
  }
}

# provider "cloudflare" {  # this was originally added throught the tfvars / shell input so its "secure"
#     api_token = "<token>" # NOW its gotten from the Parameter Store SecureString that holds the token value.
# } 


provider "aws" {
  region = "eu-central-1"
  default_tags {
    tags = { project = "uber-lite", layer = "tunnel", env = var.env, owner = "luka", managed-by = "terraform" }
  }
}