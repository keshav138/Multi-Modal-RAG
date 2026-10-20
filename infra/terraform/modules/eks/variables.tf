variable "region" {
  description = "AWS region."
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name."
  type        = string
}

variable "node_count" {
  description = "Desired worker node count."
  type        = number
}

variable "node_type" {
  description = "EC2 instance type for workers."
  type        = string
}
