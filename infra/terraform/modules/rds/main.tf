# RDS module — PostgreSQL 16 with pgvector.
#
# Amazon RDS for PostgreSQL 16.x ships the `pgvector` extension; the app's
# ingestion code runs `CREATE EXTENSION IF NOT EXISTS vector` itself, so no
# parameter group is needed here.
#
# The instance is private (no public access) and open only to the VPC CIDR
# on port 5432.

data "aws_vpc" "default" {
  id = var.vpc_id
}

resource "aws_security_group" "rds" {
  name        = "mmrag-rds"
  description = "Allow PostgreSQL from inside the VPC only"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }
}

resource "aws_db_instance" "postgres" {
  identifier = "mmrag-postgres"

  engine         = "postgres"
  engine_version = "16.4"
  instance_class = "db.t3.micro"

  db_name  = var.db_name
  username = var.db_user
  password = var.db_pass

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_encrypted     = true

  publicly_accessible = false
  skip_final_snapshot = true

  vpc_security_group_ids = [aws_security_group.rds.id]
  db_subnet_group_name   = aws_db_subnet_group.default.name

  tags = {
    Project = "multimodal-rag"
  }
}

# Subnet group across the EKS subnets (same default VPC).
resource "aws_db_subnet_group" "default" {
  name       = "mmrag-db-subnets"
  subnet_ids = var.subnet_ids
}
