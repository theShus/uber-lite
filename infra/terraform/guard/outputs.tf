output "function_name" { value = aws_lambda_function.guard.function_name }
output "function_arn" { value = aws_lambda_function.guard.arn }
output "scheduler_role_arn" { value = aws_iam_role.scheduler.arn }
output "topic_arn" { value = aws_sns_topic.guard.arn }
