output "address" { value = aws_db_instance.this.address } # dns name of RDS
output "port" { value = aws_db_instance.this.port }       # postgres port

output "master_secret_arn" { # master user creds
  description = "The RDS-managed master password. Only the db-bootstrap Job reads it (17.5)"
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "db_secret_names" { # access to our created dbs
  value = { for db, s in aws_secretsmanager_secret.db : db => s.name }
}

output "security_group_id" { value = aws_security_group.db.id } # purely for cli debugging and testing
