output "push_role_arn" { value = aws_iam_role.push.arn }
output "registry" { value = split("/", values(aws_ecr_repository.svc)[0].repository_url)[0] }
