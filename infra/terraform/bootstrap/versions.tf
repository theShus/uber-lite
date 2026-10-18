terraform {
  required_version = ">= 1.11"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }

  backend "s3" {
    key          = "bootstrap/terraform.tfstate"
    use_lockfile = true      # S3-native locking (Terraform ≥ 1.10); no DynamoDB table
  }
}
