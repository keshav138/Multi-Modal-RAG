variable "region" {
  description = "AWS region."
  type        = string
}

variable "db_name" {
  description = "Database (schema) name."
  type        = string
}

variable "db_user" {
  description = "Master username."
  type        = string
}

variable "db_pass" {
  description = "Master password."
  type        = string
  sensitive   = true
}

variable "vpc_id" {
  description = "VPC to place the instance in."
  type        = string
}

variable "subnet_ids" {
  description = "Subnets for the DB subnet group."
  type        = list(string)
}
