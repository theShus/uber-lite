resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "ul-${var.env}-rt-public" }
}

resource "aws_route" "public_default" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

# One private route table per AZ, so each AZ can use its own NAT.
resource "aws_route_table" "private" {
  for_each = toset(var.azs)
  vpc_id   = aws_vpc.this.id
  tags     = { Name = "ul-${var.env}-rt-private-${substr(each.key, -1, 1)}" }
}

resource "aws_route_table" "data" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "ul-${var.env}-rt-data" } # local route only: no internet, ever
}

resource "aws_route_table_association" "this" {
  for_each  = local.subnets
  subnet_id = aws_subnet.this[each.key].id
  route_table_id = (
    each.value.tier == "public" ? aws_route_table.public.id :
    each.value.tier == "private" ? aws_route_table.private[each.value.az].id :
    aws_route_table.data.id
  )
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [for rt in aws_route_table.private : rt.id]
  tags              = { Name = "ul-${var.env}-s3" }
}
