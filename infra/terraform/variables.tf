# Input variables for the Multi Modal RAG Terraform root module.

variable "region" {
  description = "AWS region the instance is launched in."
  type        = string
  default     = "ap-southeast-2"
}

variable "instance_type" {
  description = "EC2 instance type (m7i-flex.large = 8GB RAM, fits the ML stack)."
  type        = string
  default     = "m7i-flex.large"
}

variable "key_name" {
  description = "Name of the EC2 key pair (Terraform creates it)."
  type        = string
  default     = "mmrag-deploy"
}

variable "ssh_public_key" {
  description = "Public half of the SSH keypair. Generate locally with ssh-keygen and paste the contents of the .pub file."
  type        = string
}

variable "ssh_cidr" {
  description = "CIDR allowed to SSH into the box. Default is wide open for the demo — auth is key-only (no passwords), so this is acceptable short-term. Tighten to your own IP afterwards, e.g. 203.0.113.10/32."
  type        = string
  default     = "0.0.0.0/0"
}

variable "deploy_dir" {
  description = "Directory on the instance where Jenkins places the compose file and .env."
  type        = string
  default     = "/opt/mmrag"
}
