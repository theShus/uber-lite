variable "env" {
  type = string
  validation {
    condition     = contains(["qa", "prod"], var.env)
    error_message = "env var must be qa or prod"
  }
}

variable "region" {
    type = string
    default = "eu-central-1"
}

variable "state_bucket" {
  type = string
}

variable "engine_version" {
  description = "PostgreSQL major version. RDS picks its newest minor, and auto minor upgrades keep it current"
  type        = string
  default     = "18"
}

variable "instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "allocated_storage" {
  description = "GiB of gp3"
  type        = number
  default     = 20
}

variable "multi_az" {
  description = "A synchronous standby in a second AZ (doubles the price)"
  type        = bool
  default     = false
}

variable "backup_retention_days" {
  description = "Automated backups and the point-in-time restore window. 0 turns both off"
  type        = number
  default     = 1
}

# when you re-apply terraform it will always generate a new secert, if you didnt change the bellow version it will discard it, if it was changed 1->2 (or first time) it will apply the new value to the secert in the SecertManager
variable "db_password_versions" {
  description = "database => version. Bump one to give that service a new password "
  type        = map(number)
  default     = { rides = 1, trips = 1, payments = 1 }
}
