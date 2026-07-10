data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  tags = merge(
    {
      Project     = "cognition"
      Component   = "aws-lambda-microvm-default-runtime"
      Environment = "example"
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  awscc_tags = [
    for key, value in local.tags : {
      key   = key
      value = value
    }
  ]

  artifact_bucket_name  = lower(replace("${var.prefix}-${random_id.artifact_bucket.hex}-artifacts", "_", "-"))
  runtime_artifact_path = abspath("${path.module}/${var.runtime_artifact_path}")

  microvm_base_image_arn = coalesce(
    var.microvm_base_image_arn,
    "arn:aws:lambda:${var.aws_region}:aws:microvm-image:al2023-1",
  )

  aws_managed_all_ingress_connector_arn     = "arn:aws:lambda:${var.aws_region}:aws:network-connector:aws-network-connector:ALL_INGRESS"
  aws_managed_internet_egress_connector_arn = "arn:aws:lambda:${var.aws_region}:aws:network-connector:aws-network-connector:INTERNET_EGRESS"

  sandbox_profile_base = {
    name                           = var.profile_name
    backend                        = "aws_lambda_microvm"
    image_arn                      = awscc_lambda_microvm_image.default_runtime.image_arn
    image_version                  = awscc_lambda_microvm_image.default_runtime.latest_active_image_version
    region                         = var.aws_region
    ingress_network_connector_arns = [local.aws_managed_all_ingress_connector_arn]
    egress_mode                    = "internet"
    egress_network_connector_arns  = [local.aws_managed_internet_egress_connector_arn]
    idle_policy = {
      max_idle_duration_seconds  = var.max_idle_duration_seconds
      suspended_duration_seconds = var.suspended_duration_seconds
      auto_resume_enabled        = true
    }
    logging = {
      disabled = {}
    }
    quota = {
      max_concurrent_sessions       = var.max_concurrent_sessions
      max_session_starts_per_minute = var.max_session_starts_per_minute
    }
    maximum_duration_seconds = var.maximum_duration_seconds
    port                     = var.runtime_port
    token_expiration_minutes = var.token_expiration_minutes
    scope                    = {}
    extra                    = {}
  }

  sandbox_profile = merge(
    local.sandbox_profile_base,
    var.default_execution_role_arn == null ? {} : {
      default_execution_role_arn = var.default_execution_role_arn
    },
  )

  cognition_sandbox_profiles = {
    (var.profile_name) = local.sandbox_profile
  }
}
