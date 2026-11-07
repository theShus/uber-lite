variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "env" {
  type    = string
  default = "lab"
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "azs" {
  type    = list(string)
  default = ["eu-central-1a", "eu-central-1b"]
}

variable "nat_mode" {
  description = "How private subnets reach the internet: instance (fck-nat), gateway (one NAT GW per AZ), or none"
  type        = string
  default     = "gateway"
  validation {
    condition     = contains(["instance", "gateway", "none"], var.nat_mode)
    error_message = "nat_mode must be instance, gateway or none."
  }
}
