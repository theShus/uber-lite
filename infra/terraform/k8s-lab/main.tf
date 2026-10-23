data "terraform_remote_state" "network" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "network/terraform.tfstate"
    region = "eu-central-1"
  }
}


data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*"]
  }
}

locals {
  net = data.terraform_remote_state.network.outputs


  nodes = {
    k8s-cp1 = { subnet = 0, ip = "10.0.0.10" }
    k8s-w1  = { subnet = 0, ip = "10.0.0.11" }
    k8s-w2  = { subnet = 1, ip = "10.0.1.12" }
  }
}
