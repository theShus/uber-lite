variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "state_bucket" { type = string }

variable "repo_url" {
  description = "The uber-lite repository Argo CD reads (public, so no credentials). Must match the root chart's repoURL"
  type        = string
  # no default on purpose: `make argocd-init` writes it from gitops/bootstrap/root/values.yaml (one source of truth)
  validation {
    condition     = startswith(var.repo_url, "https://github.com/") && !strcontains(var.repo_url, "<you>")
    error_message = "repo_url must be your repository's https://github.com/... URL, as in gitops/bootstrap/root/values.yaml."
  }
}

variable "argocd_chart_version" {
  description = "argo-cd chart 10.10.5 = Argo CD v3.5.5"
  type        = string
  default     = "10.10.5"
}
