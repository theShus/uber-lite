variable "domain" { type = string }
variable "cloudflare_zone_id" { type = string }
variable "cloudflare_account_id" { type = string }

variable "env" {
  type    = string
  default = "prod"
}


# ---- Cloudflare Access in front of Argo CD's UI ------------------------
# Real values live only in terraform.tfvars (gitignored): emails and IDs stay out of the public repo.

# Zero Trust organization name: <team>.cloudflareaccess.com hosts the login page and
# the public keys cloudflared uses to check Access tokens (main.tf, origin_request.access).

variable "cloudflare_team_name" { type = string }

# Who may open Argo CD's UI. Google proves who you are; this list decides whether you get in.

variable "access_emails" { type = list(string) }

# The Google identity provider, created by hand in Zero Trust → Integrations → Identity providers,
# so its client secret never lands in Git or in this layer's state.

variable "google_idp_id" { type = string }
