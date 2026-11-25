output "argocd_version" { value = helm_release.argocd.metadata.app_version }
output "root_applications" { value = keys(local.roots) }
output "clusters" { value = keys(local.clusters) }
