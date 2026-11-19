variable "domain" { type = string }
variable "cloudflare_zone_id" { type = string }
variable "cloudflare_account_id" { type = string }

variable "env" {
  type    = string
  default = "prod"
}