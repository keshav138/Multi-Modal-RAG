# Outputs from the root module — consumed by Jenkins configuration.

output "public_ip" {
  description = "Elastic IP of the instance. Paste this into the 'ec2-host' Jenkins credential."
  value       = aws_eip.app.public_ip
}

output "instance_id" {
  description = "EC2 instance id."
  value       = aws_instance.app.id
}

output "ssh_command" {
  description = "Ready-to-use SSH command (fill in your private key path)."
  value       = "ssh -i <private-key.pem> ubuntu@${aws_eip.app.public_ip}"
}
