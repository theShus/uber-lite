variable "env" {
  description = "qa or prod string, descibing which env we are using"
  type        = string
  validation {
    condition     = contains(["qa", "prod"], var.env)
    error_message = "env var must be qa or prod"
  }
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "state_bucket" { # we set this directly in the input se we dont keep the bucket in the code
  description = "name / ARN of the s3 bucket that hold network state, so we know from what bucket to pull the output data for priv subnets"
  type        = string
}

variable "kubernetes_version" {
  description = "k8s version for the EKS input var"
  type        = string
  default     = "1.36"
}

variable "public_access_cidrs" {
  description = "who may reach public api endpoints: my home IP as a /32"
  type        = list(string)
}

variable "node_instance_types" {
  type    = list(string)
  default = ["t4g.medium"]
}

variable "node_capacity_type" {
  description = "ON_DEMAND or SPOT"
  type        = string
  default     = "ON_DEMAND"
}

# my user ARN will go here through tfvars so i can manage the cluster
variable "cluster_admins" {
  description = "name => IAM principal ARN; each gets cluster-admin through an access entry"
  type        = map(string)
}
