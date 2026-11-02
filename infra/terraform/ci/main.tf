resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # no thumbprint_list: AWS validates GitHub's certificate against its own trust store
}

data "aws_iam_policy_document" "push_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_sub_main]
    }
  }
}

resource "aws_iam_role" "push" {
  name                 = "uber-lite-ci-push"
  assume_role_policy   = data.aws_iam_policy_document.push_trust.json
  max_session_duration = 3600
}

resource "aws_ecr_repository" "svc" {
  for_each             = toset(var.services)
  name                 = "uber-lite/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true # lab only: lets `terraform destroy` remove repositories that still hold images
  image_scanning_configuration {
    scan_on_push = true
  }
}

data "aws_iam_policy_document" "push" {
  statement {
    sid       = "Login"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "PushAndPullUberLiteImages"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages", "ecr:ListImages",
    ]
    resources = [for r in aws_ecr_repository.svc : r.arn]
  }
}

resource "aws_iam_role_policy" "push" {
  name   = "ecr-push"
  role   = aws_iam_role.push.id
  policy = data.aws_iam_policy_document.push.json
}

resource "aws_ecr_lifecycle_policy" "svc" {
  for_each   = aws_ecr_repository.svc
  repository = each.value.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "keep the 15 newest commit images"
        selection    = { tagStatus = "tagged", tagPrefixList = ["sha-"], countType = "imageCountMoreThan", countNumber = 15 }
        action       = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "delete untagged images after 30 days"
        selection    = { tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = 30 }
        action       = { type = "expire" }
      },
    ]
  })
}

locals {
  github_owner    = split("/", var.github_repository)[0]
  github_repo     = split("/", var.github_repository)[1]
  github_sub_main = "repo:${local.github_owner}@${var.github_owner_id}/${local.github_repo}@${var.github_repository_id}:ref:refs/heads/main"
}
