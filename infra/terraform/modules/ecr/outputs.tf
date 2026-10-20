output "repository_url" {
  description = "Registry URL to push/pull the image."
  value       = aws_ecr_repository.repo.repository_url
}
