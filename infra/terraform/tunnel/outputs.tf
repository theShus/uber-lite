output "tunnel_id" { value = cloudflare_zero_trust_tunnel_cloudflared.api.id }

output "tunnel_token" {
  value     = data.cloudflare_zero_trust_tunnel_cloudflared_token.api.token
  sensitive = true # still stored in plain text in the state (05.7)
}
