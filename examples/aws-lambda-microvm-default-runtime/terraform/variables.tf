variable "aws_profile" {
  description = "Optional AWS CLI profile used by Terraform. Leave null for ambient credentials."
  type        = string
  default     = null
  nullable    = true
}

variable "aws_region" {
  description = "AWS region with Lambda MicroVM support."
  type        = string
  default     = "us-west-2"
}

variable "allowed_account_ids" {
  description = "Optional allow-list of AWS account IDs for provider safety."
  type        = list(string)
  default     = []
}

variable "prefix" {
  description = "Name prefix for AWS resources created by this example."
  type        = string
  default     = "cognition-microvm-runtime"

  validation {
    condition     = can(regex("^[a-zA-Z0-9][a-zA-Z0-9-]{1,42}[a-zA-Z0-9]$", var.prefix))
    error_message = "prefix must be 3-44 characters and contain only letters, numbers, and hyphens."
  }
}

variable "artifact_bucket_force_destroy" {
  description = "Allow Terraform to delete a non-empty artifact bucket during destroy."
  type        = bool
  default     = false
}

variable "runtime_artifact_path" {
  description = "Path to the packaged runtime source zip, relative to this Terraform directory."
  type        = string
  default     = "../dist/cognition-lambda-microvm-runtime.zip"
}

variable "runtime_artifact_key" {
  description = "S3 object key for the packaged runtime source zip."
  type        = string
  default     = "microvm-images/cognition-lambda-microvm-runtime.zip"
}

variable "microvm_image_name" {
  description = "Name of the Lambda MicroVM image to create."
  type        = string
  default     = "cognition-default-runtime"
}

variable "microvm_image_description" {
  description = "Description for the Lambda MicroVM image."
  type        = string
  default     = "Default Cognition command-server runtime for Lambda MicroVM sandboxes."
}

variable "microvm_base_image_arn" {
  description = "Optional Lambda-managed MicroVM base image ARN. Leave null to use al2023-1 in aws_region."
  type        = string
  default     = null
  nullable    = true
}

variable "microvm_base_image_version" {
  description = "Optional Lambda-managed MicroVM base image version."
  type        = string
  default     = null
  nullable    = true
}

variable "runtime_port" {
  description = "Runtime command server port inside the MicroVM."
  type        = number
  default     = 8080

  validation {
    condition     = var.runtime_port >= 1 && var.runtime_port <= 65535
    error_message = "runtime_port must be between 1 and 65535."
  }
}

variable "runtime_minimum_memory_mib" {
  description = "Baseline memory for the default runtime MicroVM image."
  type        = number
  default     = 2048
}

variable "build_log_retention_days" {
  description = "Retention for Lambda MicroVM image build logs."
  type        = number
  default     = 14
}

variable "profile_name" {
  description = "Name of the generated Cognition sandbox profile."
  type        = string
  default     = "default-lambda"
}

variable "maximum_duration_seconds" {
  description = "Maximum MicroVM duration for generated Cognition profile YAML."
  type        = number
  default     = 3600
}

variable "token_expiration_minutes" {
  description = "AWS proxy auth token TTL requested by Cognition."
  type        = number
  default     = 30
}

variable "max_concurrent_sessions" {
  description = "Cognition-side maximum active sandbox sessions for the generated profile/scope pair."
  type        = number
  default     = 10
}

variable "max_session_starts_per_minute" {
  description = "Cognition-side maximum sandbox session starts per minute for the generated profile/scope pair."
  type        = number
  default     = 30
}

variable "max_idle_duration_seconds" {
  description = "Maximum idle time before Lambda may suspend the MicroVM."
  type        = number
  default     = 900
}

variable "suspended_duration_seconds" {
  description = "Duration Lambda may keep a suspended MicroVM available for resume."
  type        = number
  default     = 300
}

variable "default_execution_role_arn" {
  description = "Optional sandbox execution role ARN to include in generated profile YAML."
  type        = string
  default     = null
  nullable    = true
}

variable "tags" {
  description = "Additional tags applied to AWS resources."
  type        = map(string)
  default     = {}
}
