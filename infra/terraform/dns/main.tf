# The aws.shus.world zone: created by hand in 03.2, imported in 05.5. Cloudflare delegates to its name servers,
# so recreating it would change them and break the delegation: hence prevent_destroy.
resource "aws_route53_zone" "aws" {
  name    = "aws.shus.world"
  comment = "test domain for dev ops prep"

  lifecycle { prevent_destroy = true }
}

output "zone_id" { value = aws_route53_zone.aws.zone_id }
output "name_servers" { value = aws_route53_zone.aws.name_servers }
