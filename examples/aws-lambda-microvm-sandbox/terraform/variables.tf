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
  description = "Name prefix for AWS prerequisite resources."
  type        = string
  default     = "cognition-microvm"

  validation {
    condition     = can(regex("^[a-zA-Z0-9][a-zA-Z0-9-]{1,42}[a-zA-Z0-9]$", var.prefix))
    error_message = "prefix must be 3-44 characters and contain only letters, numbers, and hyphens."
  }
}

variable "prebuilt_microvm_image_arn" {
  description = "Prebuilt Lambda MicroVM image ARN consumed by Cognition."
  type        = string

  validation {
    condition     = can(regex("^arn:aws[a-zA-Z-]*:lambda:[a-z0-9-]+:[0-9]{12}:microvm-image:.+$", var.prebuilt_microvm_image_arn))
    error_message = "prebuilt_microvm_image_arn must be a Lambda MicroVM image ARN."
  }
}

variable "prebuilt_microvm_image_version" {
  description = "Optional Lambda MicroVM image version used in generated profiles."
  type        = string
  default     = null
  nullable    = true
}

variable "profile_name" {
  description = "Name of the generated internet egress sandbox profile."
  type        = string
  default     = "default-lambda"
}

variable "vpc_profile_name" {
  description = "Name of the generated VPC egress sandbox profile."
  type        = string
  default     = "private-lambda"
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

variable "maximum_duration_seconds" {
  description = "Maximum MicroVM duration for generated Cognition profiles."
  type        = number
  default     = 3600

  validation {
    condition     = var.maximum_duration_seconds >= 1 && var.maximum_duration_seconds <= 28800
    error_message = "maximum_duration_seconds must be between 1 and 28800."
  }
}

variable "token_expiration_minutes" {
  description = "AWS proxy auth token TTL requested by Cognition."
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

variable "create_vpc_egress_connector" {
  description = "Create a Lambda Network Connector for VPC egress."
  type        = bool
  default     = false
}

variable "vpc_id" {
  description = "VPC ID for the optional VPC egress connector."
  type        = string
  default     = null
  nullable    = true
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for optional Lambda Network Connector ENIs."
  type        = list(string)
  default     = []
}

variable "vpc_egress_cidr_blocks" {
  description = "CIDR blocks allowed by the optional VPC connector security group."
  type        = list(string)
  default     = []
}

variable "control_plane_role_names" {
  description = "Existing IAM role names to attach the Cognition control-plane policy to."
  type        = list(string)
  default     = []
}

variable "additional_agent_execution_role_arns" {
  description = "Additional per-agent execution role ARNs the control plane may pass."
  type        = list(string)
  default     = []
}

variable "allow_shell_auth_tokens" {
  description = "Allow Cognition to request shell auth tokens. The runtime adapter does not require this by default."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Additional tags applied to AWS resources."
  type        = map(string)
  default     = {}
}
