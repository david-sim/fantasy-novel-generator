# =============================================================================
# infra/efs.tf
# Amazon EFS file system for persisting ChromaDB vector data across Fargate
# task restarts.  The ECS task mounts /app/data from this file system.
# =============================================================================

resource "aws_efs_file_system" "app_data" {
  creation_token   = "${local.name_prefix}-efs"
  performance_mode = "generalPurpose"
  throughput_mode  = "bursting"
  encrypted        = true

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-efs" })
}

# Security group: allow NFS (2049) only from ECS tasks
resource "aws_security_group" "efs" {
  name        = "${local.name_prefix}-efs-sg"
  description = "Allow NFS from ECS tasks"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "NFS from ECS tasks"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_task.id]
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-efs-sg" })
}

# Mount targets in both private subnets for HA access
resource "aws_efs_mount_target" "app_data" {
  count           = 2
  file_system_id  = aws_efs_file_system.app_data.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs.id]
}

# Access point isolates the container to /data, running as UID/GID 1001
# (matches the non-root user created in the Dockerfile)
resource "aws_efs_access_point" "app_data" {
  file_system_id = aws_efs_file_system.app_data.id

  root_directory {
    path = "/data"
    creation_info {
      owner_gid   = 1001
      owner_uid   = 1001
      permissions = "750"
    }
  }

  posix_user {
    gid = 1001
    uid = 1001
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-efs-ap" })
}
