variable "domain" { type = string }
variable "cloudflare_zone_id" { type = string }
variable "cloudflare_account_id" { type = string }

variable "env" {
  type    = string
  default = "prod"
}


data "aws_caller_identity" "current" {}   # account ID iz credential-a, ne hardkodovan u Gitu

ephemeral "aws_ssm_parameter" "cf_api_token" {
  arn = "arn:aws:ssm:eu-central-1:${data.aws_caller_identity.current.account_id}:parameter/uber-lite/global/cloudflare/api-token"
}

provider "cloudflare" {
  api_token = ephemeral.aws_ssm_parameter.cf_api_token.value   # provider blok prima ephemeral vrednosti
}