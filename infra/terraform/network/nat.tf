# --- Option 1: NAT instance (fck-nat), cheap, for labs ---------------------------------
data "aws_ami" "fck_nat" {
  most_recent = true
  owners      = ["568608671756"]
  filter {
    name   = "name"
    values = ["fck-nat-al2023-*-arm64-ebs"]
  }
}

resource "aws_security_group" "nat" {
  count  = var.nat_mode == "instance" ? 1 : 0
  name   = "ul-${var.env}-nat"
  vpc_id = aws_vpc.this.id

  ingress {
    description = "anything from inside the VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "nat" {
  count                       = var.nat_mode == "instance" ? 1 : 0
  ami                         = data.aws_ami.fck_nat.id
  instance_type               = "t4g.micro"
  subnet_id                   = aws_subnet.this["public-a"].id
  associate_public_ip_address = true
  source_dest_check           = false # a router forwards other hosts' packets (04.5 D3)
  vpc_security_group_ids      = [aws_security_group.nat[0].id]
  tags                        = { Name = "ul-${var.env}-fck-nat" }
}

resource "aws_route" "private_via_instance" {
  for_each               = { for az, rt in aws_route_table.private : az => rt.id if var.nat_mode == "instance" }
  route_table_id         = each.value
  destination_cidr_block = "0.0.0.0/0"
  network_interface_id   = aws_instance.nat[0].primary_network_interface_id
}

# --- Option 2: one zonal NAT gateway per AZ, the production classic -----------------------
resource "aws_eip" "nat" {
  for_each = toset(var.nat_mode == "gateway" ? var.azs : [])
  domain   = "vpc"
  tags     = { Name = "ul-${var.env}-nat-${substr(each.key, -1, 1)}" }
}

resource "aws_nat_gateway" "this" {
  for_each      = aws_eip.nat
  allocation_id = each.value.id
  subnet_id     = aws_subnet.this["public-${substr(each.key, -1, 1)}"].id
  tags          = { Name = "ul-${var.env}-nat-${substr(each.key, -1, 1)}" }
  depends_on    = [aws_internet_gateway.this]
}

resource "aws_route" "private_via_natgw" {
  for_each               = { for az, rt in aws_route_table.private : az => rt.id if var.nat_mode == "gateway" }
  route_table_id         = each.value
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[each.key].id
}
