variable "github_repository" {
  description = "owner/name of the only repository whose main branch may push images"
  type        = string
}

variable "services" {
  description = "One ECR repository per image"
  type        = list(string)
  default = [
    "rider-api", "driver-api", "location-service", "matching-service", "pricing-service",
    "trip-service", "payment-service", "notification-service", "simulator",
  ]
}

variable "github_owner_id" {
  description = "Numeric ID of the repository owner (GitHub puts it in the OIDC sub claim)"
  type        = number
}

variable "github_repository_id" {
  description = "Numeric ID of the repository (GitHub puts it in the OIDC sub claim)"
  type        = number
}
