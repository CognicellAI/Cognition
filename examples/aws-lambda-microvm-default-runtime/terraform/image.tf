resource "aws_cloudwatch_log_group" "microvm_build" {
  name              = "/aws/lambda-microvms/${var.prefix}"
  retention_in_days = var.build_log_retention_days
  tags              = local.tags
}

resource "awscc_lambda_microvm_image" "default_runtime" {
  name               = var.microvm_image_name
  base_image_arn     = local.microvm_base_image_arn
  base_image_version = var.microvm_base_image_version
  build_role_arn     = aws_iam_role.microvm_build.arn
  description        = var.microvm_image_description

  code_artifact = {
    uri = "s3://${aws_s3_bucket.artifacts.bucket}/${aws_s3_object.runtime_artifact.key}"
  }

  logging = {
    cloudwatch = {
      log_group  = aws_cloudwatch_log_group.microvm_build.name
      log_stream = "${var.prefix}-image-build"
    }
    disabled = false
  }

  egress_network_connectors = [local.aws_managed_internet_egress_connector_arn]

  cpu_configurations = [
    {
      architecture = "ARM_64"
    }
  ]

  resources = [
    {
      minimum_memory_in_mi_b = var.runtime_minimum_memory_mib
    }
  ]

  additional_os_capabilities = ["ALL"]

  environment_variables = [
    {
      key   = "COGNITION_WORKSPACE_ROOT"
      value = "/workspace"
    },
    {
      key   = "COGNITION_MAX_BODY_BYTES"
      value = "52428800"
    },
    {
      key   = "COGNITION_MAX_CAPTURE_BYTES"
      value = "2097152"
    },
    {
      key   = "PORT"
      value = tostring(var.runtime_port)
    },
  ]

  hooks = {
    microvm_image_hooks = {
      ready    = "DISABLED"
      validate = "DISABLED"
    }

    microvm_hooks = {
      run       = "DISABLED"
      resume    = "DISABLED"
      suspend   = "DISABLED"
      terminate = "DISABLED"
    }
  }

  tags = local.awscc_tags

  depends_on = [
    aws_iam_role_policy.microvm_build,
    aws_s3_object.runtime_artifact,
  ]
}
