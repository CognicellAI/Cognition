provider "aws" {
  profile             = var.aws_profile
  region              = var.aws_region
  allowed_account_ids = length(var.allowed_account_ids) > 0 ? var.allowed_account_ids : null

  default_tags {
    tags = local.tags
  }
}

provider "awscc" {
  profile = var.aws_profile
  region  = var.aws_region
}
