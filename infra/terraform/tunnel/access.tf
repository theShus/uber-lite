# Cloudflare Access in front of Argo CD's UI (18.7).
# Before a request to argocd.<domain> enters the tunnel, Cloudflare's edge sends you to Google,
# then checks the policy. Only then does the request reach cloudflared, which checks the token again (main.tf).
#
# Two resources, two questions:
#   policy      → WHO may enter (these emails)
#   application → WHAT is protected (the hostname) and HOW you prove who you are (Google)
# Both are free on the Zero Trust free plan. They stay when prod is down: the tunnel then has no
# connectors, and the hostname shows Cloudflare error 1033 instead of Argo CD.

# Reusable, account-level policy: allow exactly the listed emails. Google lets any Gmail account
# authenticate (the OAuth app's audience is External), so THIS is what keeps everyone else out.
resource "cloudflare_zero_trust_access_policy" "argocd_admins" {
  account_id = var.cloudflare_account_id
  name       = "argocd-admins-${var.env}"
  decision   = "allow"
  include    = [for e in var.access_emails : { email = { email = e } }] # one rule per email
}

# The protected hostname. Its aud (audience tag) is unique to this application: the tunnel rule
# accepts only tokens issued for it.
resource "cloudflare_zero_trust_access_application" "argocd" {
  account_id       = var.cloudflare_account_id
  name             = "argocd-${var.env}"
  type             = "self_hosted" # an app behind our own origin, not a SaaS app
  domain           = "argocd.${var.domain}"
  destinations     = [{ type = "public", uri = "argocd.${var.domain}" }] # replaces the deprecated self_hosted_domains
  session_duration = "8h"                                                # log in once per working day

  allowed_idps              = [var.google_idp_id] # Google only, no one-time PIN
  auto_redirect_to_identity = true                # straight to Google: needs exactly one IdP above

  policies = [{ id = cloudflare_zero_trust_access_policy.argocd_admins.id, precedence = 1 }]
}

resource "cloudflare_zero_trust_access_application" "grafana" {
  account_id                = var.cloudflare_account_id
  name                      = "grafana-${var.env}"
  type                      = "self_hosted"
  domain                    = "grafana.${var.domain}"
  destinations              = [{ type = "public", uri = "grafana.${var.domain}" }]
  session_duration          = "8h"

  allowed_idps              = [var.google_idp_id]
  auto_redirect_to_identity = true

  policies                  = [{ id = cloudflare_zero_trust_access_policy.argocd_admins.id, precedence = 1 }]
}
