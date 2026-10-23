variable "state_bucket" {
  description = "Bucket with the network layer's state"
  type        = string
}

variable "my_ip_cidr" {
  description = "Your home public IP/32. Only it may reach SSH (22) and the API server (6443)"
  type        = string
}
