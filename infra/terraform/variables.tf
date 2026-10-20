# Input variables for the Multi Modal RAG Terraform root module.

variable "region" {
  description = "AWS region everything is deployed into."
  type        = string
  default     = "us-east-1"
}

variable "image_name" {
  description = "Name of the ECR repository for the app image."
  type        = string
  default     = "multimodal-rag"
}

variable "cluster_name" {
  description = "Name of the EKS cluster."
  type        = string
  default     = "multimodal-rag"
}

variable "node_count" {
  description = "Number of worker nodes in the EKS node group."
  type        = number
  default     = 2
}

variable "node_type" {
  description = "EC2 instance type for EKS workers."
  type        = string
  default     = "t3.large"
}

variable "db_name" {
  description = "Name of the PostgreSQL database (schema)."
  type        = string
  default     = "rag"
}

variable "db_user" {
  description = "Master username for PostgreSQL."
  type        = string
  default     = "rag"
}

variable "db_pass" {
  description = "Master password for PostgreSQL (override via -var or tfvars)."
  type        = string
  sensitive   = true
}
