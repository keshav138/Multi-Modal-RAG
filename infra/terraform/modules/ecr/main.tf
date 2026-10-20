# ECR module — one private repository for the app image.

resource "aws_ecr_repository" "repo" {
  name = var.image_name

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Project = "multimodal-rag"
  }
}
