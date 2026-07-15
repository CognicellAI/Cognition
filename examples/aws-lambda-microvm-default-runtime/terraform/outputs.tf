output "artifact_bucket_name" {
  description = "S3 bucket containing the Lambda MicroVM runtime source artifact."
  value       = aws_s3_bucket.artifacts.bucket
}

output "runtime_artifact_s3_uri" {
  description = "S3 URI for the packaged runtime source artifact."
  value       = "s3://${aws_s3_bucket.artifacts.bucket}/${aws_s3_object.runtime_artifact.key}"
}

output "microvm_build_role_arn" {
  description = "IAM role ARN assumed by Lambda during MicroVM image builds."
  value       = aws_iam_role.microvm_build.arn
}

output "microvm_image_arn" {
  description = "Builder-owned Lambda MicroVM image ARN for Cognition sandbox profiles."
  value       = awscc_lambda_microvm_image.default_runtime.image_arn
}

output "microvm_image_version" {
  description = "Latest active version of the Lambda MicroVM image."
  value       = awscc_lambda_microvm_image.default_runtime.latest_active_image_version
}

output "microvm_image_state" {
  description = "Current Lambda MicroVM image state."
  value       = awscc_lambda_microvm_image.default_runtime.state
}

output "sandbox_profiles_yaml" {
  description = "Cognition sandbox_profiles YAML generated from the created image."
  value = yamlencode({
    sandbox_profiles = local.cognition_sandbox_profiles
  })
}
