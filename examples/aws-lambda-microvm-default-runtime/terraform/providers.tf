provider "aws" {
  profile             = var.aws_profile
  region              = var.aws_region
  allowed_account_ids = var.allowed_account_ids
}

provider "awscc" {
  profile = var.aws_profile
  region  = var.aws_region
}
