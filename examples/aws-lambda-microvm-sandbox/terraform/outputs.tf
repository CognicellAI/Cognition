output "default_agent_execution_role_arn" {
  description = "Default per-agent execution role ARN for Lambda MicroVM sandboxes."
  value       = aws_iam_role.default_agent_execution.arn
}

output "control_plane_policy_arn" {
  description = "IAM policy ARN to attach to the Cognition control-plane identity."
  value       = aws_iam_policy.control_plane.arn
}

output "attached_control_plane_role_names" {
  description = "Existing control-plane role names this module attached the policy to."
  value       = keys(aws_iam_role_policy_attachment.control_plane)
}

output "aws_managed_all_ingress_connector_arn" {
  description = "AWS-managed Lambda MicroVM ingress connector ARN."
  value       = local.aws_managed_all_ingress_connector_arn
}

output "aws_managed_internet_egress_connector_arn" {
  description = "AWS-managed Lambda MicroVM internet egress connector ARN."
  value       = local.aws_managed_internet_egress_connector_arn
}

output "vpc_egress_network_connector_arn" {
  description = "Customer-managed Lambda Network Connector ARN for VPC egress, if enabled."
  value       = try(awscc_lambda_network_connector.vpc_egress[0].arn, null)
}

output "sandbox_profiles_yaml" {
  description = "Cognition sandbox_profiles YAML generated from Terraform outputs."
  value = yamlencode({
    sandbox_profiles = local.cognition_sandbox_profiles
  })
}
