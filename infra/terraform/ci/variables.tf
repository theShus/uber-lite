variable "github_repository" {
  description = "owner/name of the github repo we are pushing from"
  type = string
}

variable "services" {
    description "list of services we want to make a ECR for"
    type = list(string)
    default = ["rider-api", "driver-api", "location-service", "matching-service", "pricing-service", "trip-service", "payment-service", "notification-service", "simulator"]
}