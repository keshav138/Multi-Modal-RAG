output "endpoint" {
  description = "PostgreSQL endpoint (hostname)."
  value       = aws_db_instance.postgres.address
}

output "port" {
  description = "PostgreSQL port."
  value       = aws_db_instance.postgres.port
}
