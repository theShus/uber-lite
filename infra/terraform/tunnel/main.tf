# One tunnel per environment. It costs nothing, so it lives "forever":
# make up/down only start and stop the connectors (cloudflared pods) that use it.

resource "cloudflare_zero_trust_tunnel_cloudflared" "api" {
  account_id = var.cloudflare_account_id
  name       = "ul-${var.env}"
  config_src = "cloudflare" # routes are managed here, not in a file inside the pods
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "api" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.api.id
  config = {
    ingress = [
      {
        hostname = "api.${var.domain}"
        service  = "https://kong-gateway-proxy.kong.svc.cluster.local:443" #dns name of the auto created service of our Kong proxy pods
        origin_request = {
          origin_server_name = "api.aws.${var.domain}" # SNI: Kong serves the *.aws certificate (16.4)
          http_host_header   = "api.aws.${var.domain}" # Kong's routes match api.aws.<domain>
        }
      },
      { service = "http_status:404" }, # required catch-all, last
    ]
  }
}

resource "cloudflare_dns_record" "api" {
  zone_id = var.cloudflare_zone_id
  name    = "api"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.api.id}.cfargotunnel.com"
  ttl     = 1 # "automatic", required for proxied records
  proxied = true
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "api" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.api.id
}