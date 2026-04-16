# Bedrock Config Example

This example shows the most important file and environment settings for AWS Bedrock.

Use `AWS_REGION`, ambient AWS credentials, or `COGNITION_BEDROCK_ROLE_ARN` for cross-account access.

## Bedrock-specific rules

- `provider: bedrock` must include `region`
- `role_arn` is valid only for `bedrock`
- Cognition builds the Bedrock LangChain model before handing execution to Deep Agents

## Recommended binding

After the provider is registered, prefer binding sessions with `provider_id` instead of relying on `model` alone.

This avoids ambiguity when multiple providers expose similarly named models.
