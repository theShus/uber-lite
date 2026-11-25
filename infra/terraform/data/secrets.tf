# One login role per service, each with its own password. Terraform generates the passwords
# but never stores them: an ephemeral value can only flow into write-only arguments (Terraform >= 1.11).
ephemeral "random_password" "db" {
  for_each = local.databases
  length   = 32
  special  = false # URL-safe: it ends up inside a postgresql:// URL (17.5)
}

resource "aws_secretsmanager_secret" "db" {
  for_each                = local.databases
  name                    = "uber-lite/${var.env}/db/${each.key}"
  description             = "Postgres login for ${each.value} (database ${each.key})"
  recovery_window_in_days = 0 # lab: deleted at once by make down, so the next make up can reuse the name
}

resource "aws_secretsmanager_secret_version" "db" {
  for_each  = local.databases
  secret_id = aws_secretsmanager_secret.db[each.key].id
  # The JSON shape AWS uses for database secrets (its rotation functions expect these keys).
  # Write-only: Terraform can't see it change. If the instance were ever replaced (a new host),
  # bump the versions so the secrets are written again.
  secret_string_wo = jsonencode({
    engine   = "postgres"
    host     = aws_db_instance.this.address
    port     = aws_db_instance.this.port
    dbname   = each.key
    username = each.key
    password = ephemeral.random_password.db[each.key].result
  })
  secret_string_wo_version = var.db_password_versions[each.key]
}
