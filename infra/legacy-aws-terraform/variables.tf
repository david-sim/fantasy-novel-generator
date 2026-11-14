# =============================================================================
# infra/variables.tf
# All input variables for the NovelEngine infrastructure.
# =============================================================================

# ── AWS / deployment ─────────────────────────────────────────────────────────

variable "aws_region" {
  description = "AWS region where all resources are provisioned"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment label (e.g. prod, staging)"
  type        = string
  default     = "prod"
}

variable "github_repo" {
  description = "GitHub repository in 'owner/repo' format – used to scope the OIDC trust policy for CI/CD"
  type        = string
  # Example: "myorg/fantansy-novel-generator"
}

# ── ECR / Docker ─────────────────────────────────────────────────────────────

variable "ecr_repository_name" {
  description = "ECR repository name – must match ECR_REPOSITORY in .github/workflows/deploy.yml"
  type        = string
  default     = "novelengine"
}

variable "image_tag" {
  description = "Docker image tag to run in ECS (CI passes the commit SHA via -var image_tag=<SHA>)"
  type        = string
  default     = "latest"
}

# ── ECS Fargate ──────────────────────────────────────────────────────────────

variable "app_port" {
  description = "Port the Streamlit container listens on"
  type        = number
  default     = 8501
}

variable "task_cpu" {
  description = "Fargate task CPU units (256 = 0.25 vCPU | 512 = 0.5 vCPU | 1024 = 1 vCPU)"
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "Fargate task memory in MiB (must be a valid cpu/memory combination)"
  type        = number
  default     = 1024
}

variable "desired_count" {
  description = "Number of ECS task replicas to keep running"
  type        = number
  default     = 1
}

# ── Application ──────────────────────────────────────────────────────────────

variable "llm_provider" {
  description = "LLM provider key passed to the app (openai | anthropic | google)"
  type        = string
  default     = "openai"

  validation {
    condition     = contains(["openai", "anthropic", "google"], var.llm_provider)
    error_message = "llm_provider must be one of: openai, anthropic, google."
  }
}

variable "llm_model" {
  description = "Default model name for the chosen LLM provider"
  type        = string
  default     = "gpt-4o"
}

variable "llm_temperature" {
  description = "Generation temperature passed to the LLM (as a string env var)"
  type        = string
  default     = "0.85"
}

variable "max_revision_loops" {
  description = "Maximum scene-writer / red-team revision cycles before forced exit"
  type        = number
  default     = 5
}

variable "llm_api_key" {
  description = "API key for the chosen LLM provider – stored in SSM SecureString, never in state plaintext"
  type        = string
  sensitive   = true
}

# ── RDS Postgres ─────────────────────────────────────────────────────────────

variable "db_username" {
  description = "RDS master username"
  type        = string
  default     = "novelengine"
}

variable "db_password" {
  description = "RDS master password (minimum 8 characters)"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.db_password) >= 8
    error_message = "db_password must be at least 8 characters."
  }
}

variable "db_instance_class" {
  description = "RDS DB instance class"
  type        = string
  default     = "db.t3.micro"
}
