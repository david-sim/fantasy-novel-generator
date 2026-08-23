# =============================================================================
# infra/outputs.tf
# Values printed after `terraform apply` – copy these into GitHub secrets
# and your local tooling.
# =============================================================================

output "app_url" {
  description = "Public URL of the Streamlit app (via ALB)"
  value       = "http://${aws_lb.main.dns_name}:${var.app_port}"
}

output "ecr_repository_url" {
  description = "Full ECR repository URL – used in Docker push commands"
  value       = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name – set as ECS_CLUSTER in .github/workflows/deploy.yml"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "ECS service name – set as ECS_SERVICE in .github/workflows/deploy.yml"
  value       = aws_ecs_service.app.name
}

output "github_actions_role_arn" {
  description = "IAM role ARN – set as the AWS_ROLE_ARN GitHub Actions secret"
  value       = aws_iam_role.github_actions.arn
}

output "rds_endpoint" {
  description = "RDS Postgres host (used to verify connectivity)"
  value       = aws_db_instance.postgres.address
  sensitive   = true
}

output "efs_id" {
  description = "EFS file system ID (for manual mount / troubleshooting)"
  value       = aws_efs_file_system.app_data.id
}
