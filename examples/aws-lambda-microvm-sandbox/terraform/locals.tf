data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  tags = merge(
    {
      Project   = "cognition"
      Component = "aws-lambda-microvm-sandbox"
      ManagedBy = "terraform"
    },
    var.tags,
  )

  awscc_tags = [
    for key, value in local.tags : {
      key   = key
      value = value
    }
  ]

  aws_managed_all_ingress_connector_arn     = "arn:aws:lambda:${var.aws_region}:aws:network-connector:aws-network-connector:ALL_INGRESS"
  aws_managed_internet_egress_connector_arn = "arn:aws:lambda:${var.aws_region}:aws:network-connector:aws-network-connector:INTERNET_EGRESS"

  vpc_egress_connector_arns = (
    var.create_vpc_egress_connector
    ? [awscc_lambda_network_connector.vpc_egress[0].arn]
    : []
  )

  execution_role_arns = concat(
    [aws_iam_role.default_agent_execution.arn],
    var.additional_agent_execution_role_arns,
  )

  control_plane_token_actions = (
    var.allow_shell_auth_tokens
    ? ["lambda:CreateMicrovmAuthToken", "lambda:CreateMicrovmShellAuthToken"]
    : ["lambda:CreateMicrovmAuthToken"]
  )

  sandbox_profile_idle_policy = {
    max_idle_duration_seconds  = var.max_idle_duration_seconds
    suspended_duration_seconds = var.suspended_duration_seconds
    auto_resume_enabled        = true
  }

  internet_sandbox_profile = {
    (var.profile_name) = {
      backend                        = "aws_lambda_microvm"
      image_arn                      = var.prebuilt_microvm_image_arn
      image_version                  = var.prebuilt_microvm_image_version
      region                         = var.aws_region
      ingress_network_connector_arns = [local.aws_managed_all_ingress_connector_arn]
      egress_mode                    = "internet"
      egress_network_connector_arns  = [local.aws_managed_internet_egress_connector_arn]
      idle_policy                    = local.sandbox_profile_idle_policy
      maximum_duration_seconds       = var.maximum_duration_seconds
      port                           = var.runtime_port
      token_expiration_minutes       = var.token_expiration_minutes
      default_execution_role_arn     = aws_iam_role.default_agent_execution.arn
      scope                          = {}
      extra                          = {}
    }
  }

  vpc_sandbox_profile = (
    var.create_vpc_egress_connector
    ? {
      (var.vpc_profile_name) = {
        backend                        = "aws_lambda_microvm"
        image_arn                      = var.prebuilt_microvm_image_arn
        image_version                  = var.prebuilt_microvm_image_version
        region                         = var.aws_region
        ingress_network_connector_arns = [local.aws_managed_all_ingress_connector_arn]
        egress_mode                    = "vpc"
        egress_network_connector_arns  = local.vpc_egress_connector_arns
        idle_policy                    = local.sandbox_profile_idle_policy
        maximum_duration_seconds       = var.maximum_duration_seconds
        port                           = var.runtime_port
        token_expiration_minutes       = var.token_expiration_minutes
        default_execution_role_arn     = aws_iam_role.default_agent_execution.arn
        scope                          = {}
        extra                          = {}
      }
    }
    : {}
  )

  cognition_sandbox_profiles = merge(
    local.internet_sandbox_profile,
    local.vpc_sandbox_profile,
  )
}
