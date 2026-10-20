# Terraform entrypoint for the Multi Modal RAG cloud footprint.
#
# Simplified architecture: ONE EC2 instance runs the whole stack via
# docker compose (app + pgvector + Prometheus + Grafana).  No EKS, no RDS,
# no ECR — the app image is built by Jenkins and pushed to Docker Hub.
#
# Terraform's job is only day-0 / rare infrastructure: the instance, its
# security group, the SSH key, and the cloud-init script that installs
# Docker.  Everything app-related is delivered by Jenkins over SSH.
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
  # or DynamoDB table needed.  Authentication uses the
  # TF_API_TOKEN / TF_TOKEN_app_terraform_io environment variable.
  cloud {
    organization = "keshav-org" # your HCP Terraform organization
    workspaces {
      name = "multimodal-rag"   # the workspace holding this state
    }
  }
}

provider "aws" {
  region = var.region
}

# ── Latest Ubuntu LTS AMI ─────────────────────────────────────────────

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name = "name"
    # Ubuntu switched AMI naming to the hvm-ssd-gp3 prefix for newer
    # releases, so match both naming generations and take the newest.
    values = [
      "ubuntu/images/hvm-ssd/ubuntu-noble-24.04-amd64-server-*",
      "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*",
    ]
  }
}

# ── Network: everything open for the demo; SSH stays key-only ────────

resource "aws_security_group" "mmrag" {
  name        = "mmrag-ec2"
  description = "Multi Modal RAG single-instance stack"

  ingress {
    description = "SSH - key-only auth, open for the demo. Set ssh_cidr to your IP for real use"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }
  ingress {
    description = "API"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # demo-level; tighten to var.my_ip for real use
  }
  ingress {
    description = "Grafana"
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "Prometheus"
    from_port   = 9090
    to_port     = 9090
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── SSH key: Jenkins (and you) log in with the private half ──────────

resource "aws_key_pair" "deploy" {
  key_name   = var.key_name
  public_key = var.ssh_public_key
}

# ── The instance ──────────────────────────────────────────────────────

resource "aws_instance" "app" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.deploy.key_name
  vpc_security_group_ids = [aws_security_group.mmrag.id]

  root_block_device {
    volume_size = 30 # images + model cache + docker volumes
    volume_type = "gp3"
  }

  # Cloud-init installs Docker + compose and prepares the deploy dir.
  # No code is cloned and no secrets are baked here — Jenkins delivers both.
  user_data = templatefile("${path.module}/files/setup.sh", {
    deploy_dir = var.deploy_dir
  })

  tags = {
    Name    = "mmrag"
    Project = "multimodal-rag"
  }
}

# ── Elastic IP: stable SSH target for Jenkins ─────────────────────────

resource "aws_eip" "app" {
  instance = aws_instance.app.id
  domain   = "vpc"
}
