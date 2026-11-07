variable "github_oidc_sub_prefix" {
  description = "GitHub OIDC sub prefix for this repo (owner/repo with immutable IDs)"
  type        = string
}

variable "services" {
  description = "list of services we want to make a ECR for"
  type        = list(string)
  default     = ["rider-api", "driver-api", "location-service", "matching-service", "pricing-service",
    "trip-service", "payment-service", "notification-service", "simulator"]
}
