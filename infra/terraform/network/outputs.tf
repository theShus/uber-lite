output "vpc_id" { value = aws_vpc.this.id }
output "vpc_cidr" { value = aws_vpc.this.cidr_block }
output "public_subnet_ids" { value = [for k, s in aws_subnet.this : s.id if local.subnets[k].tier == "public"] }
output "private_subnet_ids" { value = [for k, s in aws_subnet.this : s.id if local.subnets[k].tier == "private"] }
output "data_subnet_ids" { value = [for k, s in aws_subnet.this : s.id if local.subnets[k].tier == "data"] }
output "private_route_table_ids" { value = [for rt in aws_route_table.private : rt.id] }
