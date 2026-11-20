
data "terraform_remote_state" "network" {
    backend = "s3"
    config = { bucket = var.state_bucket, key = "network/terraform.tfstate", region = var.region }
}

locals {
    name = "ul-${var.env}"
    net = data.terraform_remote_state.network.outputs
    # each service own its own db so services cant read each others dbs, all on one server, no need for each db to have its own server
    databases = { rides = "rider-api", trips = "trip-service", payments = "payment-service" }
}

# The cluster's subnets: nodes, and pods (the VPC CNI gives them addresses from here)
data "aws_subnet" "private" {
  for_each = toset(local.net.private_subnet_ids)
  id = each.value
}

# this groups AZs where AWS will place EINs for your RDS, aka you pick with AZs/Subnet will hold your RDS DBs. Like NodeGroup for EKS.
resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = local.net.data_subnet_ids # the data tier: no route to the internet (04.5)
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "Postgres, from the private subnets only" # EC2 rejects apostrophes and non-ASCII here
  vpc_id      = local.net.vpc_id
  # No egress rules: Terraform removes AWS's default allow-all. Replies to allowed connections
}

# we open the ingress to all IPs from the priv subnets, so we take the entire cidr block. 
# if we want to then alter allow access to RDS for only specific pods we will do one of the following:
# 1. create a NetworkPolicy egress for only those 3
# 2. instead of reg SG we make SecurityGroup for Pods, but this is not too good because then we bind the terraform layer with the eks layer (need pod name)
# 3. we make ServiceAccoutns that can access the db and give only to wanted pods

resource "aws_vpc_security_group_ingress_rule" "postgres" {
  for_each          = data.aws_subnet.private
  security_group_id = aws_security_group.db.id
  description       = "Postgres from the private subnet in ${each.value.availability_zone}"
  cidr_ipv4         = each.value.cidr_block
  ip_protocol       = "tcp"
  from_port         = 5432 # standard Postgres port
  to_port           = 5432
}

# this is like postgresql.conf file, you adjust the engine version and some configs here
resource "aws_db_parameter_group" "this" {
  name   = "${local.name}-postgres${var.engine_version}"
  family = "postgres${var.engine_version}"

  parameter {
    name  = "rds.force_ssl"
    value = "1" # already the default since PostgreSQL 15 on RDS: written out because it's a security control
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "500" # log every statement slower than 500 ms
  }

  lifecycle {
    create_before_destroy = true # a major upgrade needs a new family: create the new group first
  }
}


# Lab trade-offs, reviewed like code. Each expires at the end of the track.
# trivy:ignore:AWS-0077:exp:2027-01-31 -- 1-day backups: the database is rebuilt every session; production keeps 7-35 days
# trivy:ignore:AWS-0177:exp:2027-01-31 -- no deletion protection: make down destroys it every evening
resource "aws_db_instance" "this" {
  identifier               = local.name
  engine                   = "postgres"
  engine_version           = var.engine_version
  engine_lifecycle_support = "open-source-rds-extended-support-disabled" # the API default enrolls you in paid Extended Support
  instance_class           = var.instance_class

  allocated_storage = var.allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true # with the AWS-managed aws/rds key: free

  username                            = "postgres"
  manage_master_user_password         = true # RDS generates it, keeps it in Secrets Manager, rotates it every 7 days
  iam_database_authentication_enabled = true # free: 17.2 Part F

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id] # we tie the SG to the data subnets
  publicly_accessible    = false
  multi_az               = var.multi_az
  parameter_group_name   = aws_db_parameter_group.this.name

  backup_retention_period    = var.backup_retention_days
  backup_window              = "02:00-02:30"         # UTC
  maintenance_window         = "sun:03:00-sun:03:30" # UTC, never overlapping the backup window
  auto_minor_version_upgrade = true
  apply_immediately          = true # lab: changes now, not in the next maintenance window
  copy_tags_to_snapshot      = true

  # Rebuilt every session. A production database flips all three (17.3 §1.4).
  deletion_protection      = false
  skip_final_snapshot      = true
  delete_automated_backups = true
}
