# How private subnets reach the internet, chosen by var.nat_mode:
#   instance: one fck-nat instance (cheap, for labs), in modules/nat-instance
#   gateway:  one zonal NAT gateway per AZ (the production classic)
#   none:     no internet egress at all (the S3 gateway endpoint still works)

# --- Option 1: NAT instance (fck-nat) ------------------------------------------------------
module "nat_instance" {
  source          = "../modules/nat-instance"
  count           = var.nat_mode == "instance" ? 1 : 0
  name            = "ul-${var.env}-fck-nat"
  vpc_id          = aws_vpc.this.id
  vpc_cidr        = var.vpc_cidr
  subnet_id       = aws_subnet.this["public-a"].id
  route_table_ids = { for az, rt in aws_route_table.private : az => rt.id }
}

# --- Option 2: one zonal NAT gateway per AZ -------------------------------------------------
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
  depends_on    = [aws_internet_gateway.this] # needs the IGW, but doesn't reference it
}

resource "aws_route" "private_via_natgw" {
  for_each               = { for az, rt in aws_route_table.private : az => rt.id if var.nat_mode == "gateway" }
  route_table_id         = each.value
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[each.key].id
}
