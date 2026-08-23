# =============================================================================
# infra/main.tf
# Provider, backend, and shared locals.
# =============================================================================

terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Uncomment and populate to store state remotely (strongly recommended for
  # any shared / production environment).
  #
  # backend "s3" {
  #   bucket         = "novelengine-tfstate"
  #   key            = "prod/terraform.tfstate"
  #   region         = "us-east-1"
  #   encrypt        = true
  #   dynamodb_table = "novelengine-tfstate-lock"
  # }
}

provider "aws" {
  region = var.aws_region
}

# ---------------------------------------------------------------------------
# Data sources available to all modules in this root configuration
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

# ---------------------------------------------------------------------------
# Shared locals
# ---------------------------------------------------------------------------

locals {
  name_prefix = "novelengine-${var.environment}"

  # Slice to exactly two AZs for subnets / RDS multi-AZ
  azs = slice(data.aws_availability_zones.available.names, 0, 2)

  common_tags = {
    Project     = "NovelEngine"
    Environment = var.environment
    ManagedBy   = "Terraform"
    Repository  = var.github_repo
  }

  # Map LLM provider → expected environment variable name in the container
  llm_env_var_name = {
    openai    = "OPENAI_API_KEY"
    anthropic = "ANTHROPIC_API_KEY"
    google    = "GOOGLE_API_KEY"
  }
}
