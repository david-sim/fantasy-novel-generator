# =============================================================================
# infra/ecs.tf
# ECS Fargate cluster, CloudWatch log group, task definition, and service.
#
# First-time bootstrap note:
#   The task definition references an ECR image.  On the very first
#   `terraform apply` no image exists yet, so the initial ECS deployment will
#   fail to pull and tasks will stay in PENDING.  Run the GitHub Actions
#   workflow once to push an image, then the service will stabilize.
# =============================================================================

# ── Cluster ───────────────────────────────────────────────────────────────────

resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = local.common_tags
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

# ── CloudWatch Log Group ──────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${local.name_prefix}"
  retention_in_days = 30
  tags              = local.common_tags
}

# ── Task Definition ───────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "app" {
  family                   = "${local.name_prefix}-task"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "novelengine"
      image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
      essential = true

      portMappings = [
        {
          containerPort = var.app_port
          protocol      = "tcp"
        }
      ]

      # Static environment variables (non-secret configuration)
      environment = [
        { name = "CHROMA_PATH",          value = "/app/data/chroma_db" },
        { name = "LLM_PROVIDER",         value = var.llm_provider },
        { name = "LLM_MODEL",            value = var.llm_model },
        { name = "LLM_TEMPERATURE",      value = var.llm_temperature },
        { name = "MAX_REVISION_LOOPS",   value = tostring(var.max_revision_loops) },
        { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
        { name = "PYTHONUNBUFFERED",     value = "1" },
      ]

      # Secrets are pulled from SSM at container start; never appear in
      # the task definition JSON stored in ECS.
      # local.llm_env_var_name maps provider → correct env var (e.g. OPENAI_API_KEY)
      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = aws_ssm_parameter.db_url.arn
        },
        {
          name      = local.llm_env_var_name[var.llm_provider]
          valueFrom = aws_ssm_parameter.llm_api_key.arn
        },
      ]

      mountPoints = [
        {
          sourceVolume  = "app-data"
          containerPath = "/app/data"
          readOnly      = false
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }

      # Streamlit health endpoint used by the ALB and Docker HEALTHCHECK
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:${var.app_port}/_stcore/health')\" || exit 1"]
        interval    = 30
        timeout     = 10
        retries     = 3
        startPeriod = 60
      }
    }
  ])

  # EFS volume for ChromaDB persistence
  volume {
    name = "app-data"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.app_data.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.app_data.id
        iam             = "ENABLED"
      }
    }
  }

  tags = local.common_tags
}

# ── Service ───────────────────────────────────────────────────────────────────

resource "aws_ecs_service" "app" {
  name            = "${local.name_prefix}-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs_task.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "novelengine"
    container_port   = var.app_port
  }

  # Ensure the listener exists before the service tries to register targets
  depends_on = [aws_lb_listener.app]

  lifecycle {
    # Prevent Terraform from reverting the task definition after CI deploys
    # a new image via `aws ecs update-service --force-new-deployment`.
    ignore_changes = [task_definition]
  }

  tags = local.common_tags
}
