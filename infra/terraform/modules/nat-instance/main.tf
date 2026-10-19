# A fck-nat instance: a small Linux router that NATs the private subnets' traffic to the internet.
data "aws_ami" "fck_nat" {
  most_recent = true
  owners      = ["568608671756"] # the fck-nat project's AWS account
  filter {
    name   = "name"
    values = ["fck-nat-al2023-*-arm64-ebs"]
  }
}

resource "aws_security_group" "this" {
  name        = var.name
  description = "fck-nat: forward anything from inside the VPC"
  vpc_id      = var.vpc_id
  tags        = { Name = var.name }

  ingress {
    description = "anything from inside the VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    description = "anything to the internet"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "this" {
  ami                         = data.aws_ami.fck_nat.id
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  associate_public_ip_address = true
  source_dest_check           = false # a router forwards other hosts' packets (04.5 D3)
  vpc_security_group_ids      = [aws_security_group.this.id]
  tags                        = { Name = var.name }
}

# The default route of every private route table points at the instance's network interface.
resource "aws_route" "default" {
  for_each               = var.route_table_ids
  route_table_id         = each.value
  destination_cidr_block = "0.0.0.0/0"
  network_interface_id   = aws_instance.this.primary_network_interface_id
}
