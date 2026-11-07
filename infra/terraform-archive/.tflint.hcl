plugin "terraform" {
  enabled = true
  preset  = "recommended"
}
plugin "aws" {
  enabled = true
  version = "0.40.0"            # check https://github.com/terraform-linters/tflint-ruleset-aws/releases for newer
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}
