locals {
  name = "ul-guard"
}

data "aws_caller_identity" "me" {}


# ---- IM roles that will give lambda access to call EKS get/print funs, and SNS call funs ----

data "aws_iam_policy_document" "lambda_trust" { # policy that allows lambda service to aquire the role that this policy is attached to
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" { # role that the lambda can take, we will attach more policies to it
  name               = "${local.name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "lambda" { # policy that allows role to describe eks and call SNS
  statement {
    sid = "ReadWhatBills"
    actions = [
      "eks:ListClusters",
      "ec2:DescribeInstances",
      "ec2:DescribeNatGateways",
      "ec2:DescribeAddresses",
      "ec2:DescribeVolumes",
      "elasticloadbalancing:DescribeLoadBalancers",
      "rds:DescribeDBInstances",
    ]
    resources = ["*"] # these List/Describe calls don't support resource-level permissions
  }
  statement {
    sid       = "Report"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.guard.arn]
  }
  statement {
    sid       = "OwnLogGroupOnly" # no logs:CreateLogGroup on purpose
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.guard.arn}:*"]
  }
}

resource "aws_iam_role_policy" "lambda" { # we attach the new action policy to the previously created iam role
  name   = "guard"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

data "archive_file" "code" { # we zip our python code to send it to lambda fun
  type        = "zip"
  source_file = "${path.module}/src/handler.py"
  output_path = "${path.module}/build/guard.zip"
}
# ----

# ---- logging for lambda ----
resource "aws_cloudwatch_log_group" "guard" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 1 # created before the function, so Lambda never makes a never-expiring one
}
# ----

# ---- the report channel ----
# trivy:ignore:AWS-0136:exp:2027-01-31 -- the reports carry resource IDs, not secrets; a customer KMS key would cost $1/month
resource "aws_sns_topic" "guard" { # sns topic that we will put the email sub into
  name              = local.name
  kms_master_key_id = "alias/aws/sns" # encrypted at rest with the AWS-managed key: free
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.guard.arn
  protocol  = "email"
  endpoint  = var.alert_email # stays "pending confirmation" until you click the link
}
# --------

resource "aws_lambda_function" "guard" {
  function_name    = local.name
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.14"
  architectures    = ["arm64"]
  handler          = "handler.lambda_handler" # name of our handler.py file, and name of fun we want to call !
  filename         = data.archive_file.code.output_path
  source_code_hash = data.archive_file.code.output_base64sha256
  timeout          = 60
  memory_size      = 128

  environment {
    variables = {
      TOPIC_ARN = aws_sns_topic.guard.arn
      REGIONS   = join(",", var.regions)
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.guard.name
  }

  depends_on = [aws_iam_role_policy.lambda] # make the role first then attach it while creating lambda, otherwise it can make role after and then attach it
}



# ---- the schedule ----

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.me.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

resource "aws_iam_role_policy" "scheduler" {
  name = "invoke-guard"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "lambda:InvokeFunction", Resource = aws_lambda_function.guard.arn }] # this is a more direct way to write the policy doc
  })                                                                                                               # both are just json in-line policies, they dont create as phisical objs in aws just jsons, but one is a direct other is a more pretty terraform obj way
}

resource "aws_scheduler_schedule" "nightly" {
  for_each = {
    mon-sat = { cron = "cron(0 23 ? * MON-SAT *)", heartbeat = false }
    sunday  = { cron = "cron(0 23 ? * SUN *)", heartbeat = true }
  }

  name                         = "${local.name}-${each.key}"
  schedule_expression          = each.value.cron
  schedule_expression_timezone = "Europe/Belgrade" # 23:00 local, summer and winter

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.guard.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ heartbeat = each.value.heartbeat })

    retry_policy {
      maximum_retry_attempts = 2
    }
  }
}
