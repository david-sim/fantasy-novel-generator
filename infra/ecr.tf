# =============================================================================
# infra/ecr.tf
# Elastic Container Registry repository for the NovelEngine Docker image.
# =============================================================================

resource "aws_ecr_repository" "app" {
  name                 = var.ecr_repository_name
  image_tag_mutability = "MUTABLE"   # allows re-pushing :latest

  image_scanning_configuration {
    scan_on_push = true   # automatic CVE scanning on every push
  }

  tags = local.common_tags
}

# Keep only the 10 most-recent images; expire everything older to control cost.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire images beyond the 10 most recent"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = { type = "expire" }
      }
    ]
  })
}
