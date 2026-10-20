# Outputs from the root module — used by the GitHub Actions deploy job and
# by whoever populates infra/k8s/secret.yaml.

output "ecr_repository_url" {
  description = "Registry URL the CI workflow pushes images to."
  value       = module.ecr.repository_url
}

output "eks_cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = module.eks.cluster_endpoint
}

output "eks_cluster_name" {
  description = "EKS cluster name (used with aws eks update-kubeconfig)."
  value       = module.eks.cluster_name
}

output "db_endpoint" {
  description = "PostgreSQL endpoint for the DATABASE_URL secret."
  value       = module.rds.endpoint
}

output "db_port" {
  description = "PostgreSQL port (5432)."
  value       = module.rds.port
}
