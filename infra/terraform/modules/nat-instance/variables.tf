variable "name" { type = string }
variable "vpc_id" { type = string }
variable "vpc_cidr" { type = string }
variable "subnet_id" { type = string }
variable "route_table_ids" { type = map(string) } # key => route table id
variable "instance_type" {
  type    = string
  default = "t4g.micro"
}
