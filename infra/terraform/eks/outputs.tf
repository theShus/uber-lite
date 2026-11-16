output "cluster_name" { value = module.eks.cluster_name }
output "cluster_version" { value = module.eks.cluster_version }
output "cluster_endpoint" { value = module.eks.cluster_endpoint }
output "node_security_group_id" { value = module.eks.node_security_group_id }
output "cluster_primary_security_group_id" { value = module.eks.cluster_primary_security_group_id }

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region} --alias ${var.env}"
}
