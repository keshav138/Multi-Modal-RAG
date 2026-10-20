# Terraform entrypoint for the Multi Modal RAG cloud footprint.
#
# Composes three modules:
#   - ecr : container registry for the app image (CI workflow pushes here)
#   - rds : PostgreSQL 16 with the pgvector extension available
#   - eks : Kubernetes cluster that runs the manifests in infra/k8s/
#
# The EKS and RDS modules use the AWS default VPC for simplicity — a real
# deployment should give them a dedicated, locked-down VPC instead.
#
# Usage:
#   terraform init && terraform plan && terraform apply

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # HCP Terraform stores the state file and handles locking — no S3 bucket
  # or DynamoDB table needed.  Create the organization + workspace in HCP
  # first, then fill both names below.  Authentication uses the
  # TF_API_TOKEN / TF_TOKEN_app_terraform_io environment variable.
  cloud {
    organization = "keshav-org" # your HCP Terraform organization
    workspaces {
      name = "multimodal-rag"                # the workspace holding this state
    }
  }
}

provider "aws" {
  region = var.region
}

module "ecr" {
  source = "./modules/ecr"

  image_name = var.image_name
}

module "rds" {
  source = "./modules/rds"

  region     = var.region
  db_name    = var.db_name
  db_user    = var.db_user
  db_pass    = var.db_pass
  vpc_id     = module.eks.vpc_id
  subnet_ids = module.eks.subnet_ids
}

module "eks" {
  source = "./modules/eks"

  region       = var.region
  cluster_name = var.cluster_name
  node_count   = var.node_count
  node_type    = var.node_type
}
