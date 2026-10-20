output "cluster_name" {
  description = "EKS cluster name."
  value       = aws_eks_cluster.cluster.name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = aws_eks_cluster.cluster.endpoint
}

output "vpc_id" {
  description = "VPC the cluster (and RDS) live in."
  value       = data.aws_vpc.default.id
}

output "subnet_ids" {
  description = "Subnets used by the cluster, shared with the RDS module."
  value       = data.aws_subnets.default.ids
}
