# =============================================================================
# infra/iam.tf
# IAM roles and policies for:
#   1. ECS task execution role  – lets ECS pull images and read SSM secrets
#   2. ECS task role            – runtime permissions for the app container
#   3. GitHub Actions role      – OIDC-based CI/CD role (no static credentials)
# =============================================================================

# ---------------------------------------------------------------------------
# Shared assume-role policy for ECS tasks
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# ---------------------------------------------------------------------------
# 1. ECS Task Execution Role
#    Used by the ECS agent to pull the Docker image from ECR and ship logs to
#    CloudWatch.  Also grants access to the SSM SecureString parameters so
#    they can be injected as container secrets at startup.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "ecs_execution" {
  name               = "${local.name_prefix}-ecs-exec-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
  tags               = local.common_tags
}

# AWS-managed policy covers ECR pull + CloudWatch Logs
resource "aws_iam_role_policy_attachment" "ecs_exec_managed" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Minimal SSM read access for the two SecureString parameters
resource "aws_iam_role_policy" "ecs_exec_ssm" {
  name = "${local.name_prefix}-exec-ssm"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadSSMSecrets"
        Effect   = "Allow"
        Action   = ["ssm:GetParameters", "kms:Decrypt"]
        Resource = [
          aws_ssm_parameter.db_url.arn,
          aws_ssm_parameter.llm_api_key.arn,
        ]
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# 2. ECS Task Role
#    Attached to the running container; grants only what the app needs at
#    runtime (EFS mount).  Add Bedrock / S3 / SES permissions here as needed.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "ecs_task" {
  name               = "${local.name_prefix}-ecs-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy" "ecs_task_efs" {
  name = "${local.name_prefix}-task-efs"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EFSMount"
        Effect = "Allow"
        Action = [
          "elasticfilesystem:ClientMount",
          "elasticfilesystem:ClientWrite",
          "elasticfilesystem:DescribeMountTargets",
        ]
        Resource = aws_efs_file_system.app_data.arn
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# 3. GitHub Actions CI/CD Role  (OIDC – no static access keys required)
#
#    Bootstrap steps (one-time, done by an AWS admin):
#      a. terraform apply   ← creates the OIDC provider + this role
#      b. Copy the "github_actions_role_arn" output value
#      c. Add it as a GitHub secret named AWS_ROLE_ARN in your repository
#
#    After that, every push to main authenticates via OIDC with no secrets.
# ---------------------------------------------------------------------------

# The GitHub OIDC provider needs to exist once per AWS account.
# If your account already has it (check with: aws iam list-open-id-connect-providers)
# you can import it: terraform import aws_iam_openid_connect_provider.github <arn>
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  # GitHub's OIDC thumbprints (stable – see github.com/aws-actions/configure-aws-credentials)
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

data "aws_iam_policy_document" "github_actions_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    # Only tokens issued for pushes to the main branch of the configured repo
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${local.name_prefix}-github-actions-role"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy" "github_actions_cicd" {
  name = "${local.name_prefix}-cicd"
  role = aws_iam_role.github_actions.id

  # ARNs are constructed from known naming conventions to avoid a circular
  # dependency between iam.tf and ecs.tf.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ECRAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "ECRPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = aws_ecr_repository.app.arn
      },
      {
        Sid    = "ECSUpdateService"
        Effect = "Allow"
        Action = [
          "ecs:UpdateService",
          "ecs:DescribeServices",
        ]
        Resource = [
          "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:cluster/${local.name_prefix}-cluster",
          "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:service/${local.name_prefix}-cluster/${local.name_prefix}-service",
        ]
      },
      {
        # ecs wait services-stable needs wildcard DescribeServices
        Sid      = "ECSDescribeAll"
        Effect   = "Allow"
        Action   = ["ecs:DescribeServices"]
        Resource = "*"
      }
    ]
  })
}
