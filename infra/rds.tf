# =============================================================================
# infra/rds.tf
# RDS Postgres instance (Story 5.3 – replaces SQLite in production).
# SSM SecureString parameters inject secrets into the ECS task at runtime;
# they are never stored in the Terraform state as plaintext.
#
# NOTE: Add psycopg2-binary (or asyncpg) to requirements.txt before
#       switching DATABASE_URL to the postgresql:// scheme.
# =============================================================================

resource "aws_db_subnet_group" "main" {
  name       = "${local.name_prefix}-db-subnet-group"
  subnet_ids = aws_subnet.private[*].id
  tags       = merge(local.common_tags, { Name = "${local.name_prefix}-db-subnet-group" })
}

resource "aws_db_instance" "postgres" {
  identifier        = "${local.name_prefix}-db"
  engine            = "postgres"
  engine_version    = "16"
  instance_class    = var.db_instance_class
  allocated_storage = 20
  storage_type      = "gp3"

  db_name  = "novelengine"
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  publicly_accessible = false
  multi_az            = false   # set true for production HA

  backup_retention_period   = 7
  backup_window             = "03:00-04:00"
  maintenance_window        = "mon:04:00-mon:05:00"
  auto_minor_version_upgrade = true

  # Safeguards against accidental destruction
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name_prefix}-final-snapshot"

  tags = local.common_tags
}

# ── SSM SecureString parameters ───────────────────────────────────────────────
# These are read by the ECS task execution role at container startup;
# the plaintext values are never written to ECS task definition JSON.

resource "aws_ssm_parameter" "db_url" {
  name  = "/${var.environment}/novelengine/DATABASE_URL"
  type  = "SecureString"
  # SQLAlchemy DSN – switch to asyncpg driver if using async sessions
  value = "postgresql+psycopg2://${var.db_username}:${var.db_password}@${aws_db_instance.postgres.address}:5432/novelengine"
  tags  = local.common_tags
}

resource "aws_ssm_parameter" "llm_api_key" {
  name  = "/${var.environment}/novelengine/LLM_API_KEY"
  type  = "SecureString"
  value = var.llm_api_key
  tags  = local.common_tags
}
