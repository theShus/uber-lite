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
