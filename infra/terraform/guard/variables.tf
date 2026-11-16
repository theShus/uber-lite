variable "alert_email" {
  description = "Where the guard sends its report. Confirm the subscription email once."
  type        = string
}

variable "regions" {
  description = "Regions the guard checks (the same as whats-running.sh)"
  type        = list(string)
  default     = ["eu-central-1", "eu-north-1", "us-east-1"]
}
