locals {
  # One entry per tier: how many bits to add to the /16, and which block number the first AZ gets.
  tiers = {
    public  = { newbits = 8, first = 0 }   # /24s: 10.0.0.0, 10.0.1.0
    private = { newbits = 3, first = 1 }   # /19s: 10.0.32.0, 10.0.64.0
    data    = { newbits = 8, first = 200 } # /24s: 10.0.200.0, 10.0.201.0
  }

  # Flatten tiers × AZs into one map: "public-a" => { tier, az, cidr }
  subnets = merge([
    for tier, t in local.tiers : {
      for i, az in var.azs :
      "${tier}-${substr(az, -1, 1)}" => {
        tier = tier
        az   = az
        cidr = cidrsubnet(var.vpc_cidr, t.newbits, t.first + i)
      }
    }
  ]...)
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "ul-${var.env}" }
}

resource "aws_subnet" "this" {
  for_each = local.subnets

  vpc_id            = aws_vpc.this.id
  cidr_block        = each.value.cidr
  availability_zone = each.value.az

  tags = merge(
    { Name = "ul-${var.env}-${each.key}", tier = each.value.tier },
    # Tags the AWS Load Balancer Controller uses to find subnets (topic 16):
    each.value.tier == "public" ? { "kubernetes.io/role/elb" = "1" } : {},
    each.value.tier == "private" ? { "kubernetes.io/role/internal-elb" = "1" } : {}
  )
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "ul-${var.env}-igw" }
}
