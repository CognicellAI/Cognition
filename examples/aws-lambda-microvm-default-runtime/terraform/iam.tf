data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = [
      "sts:AssumeRole",
      "sts:TagSession",
    ]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "microvm_build" {
  name               = "${var.prefix}-build"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = local.tags
}

data "aws_iam_policy_document" "microvm_build" {
  statement {
    sid = "ReadMicrovmBuildArtifacts"

    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]

    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
  }

  statement {
    sid = "CreateMicrovmBuildLogGroups"

    actions = [
      "logs:CreateLogGroup",
    ]

    resources = ["arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda-microvms/${var.prefix}*"]
  }

  statement {
    sid = "WriteMicrovmBuildLogStreams"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = ["arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda-microvms/${var.prefix}*:log-stream:*"]
  }
}

resource "aws_iam_role_policy" "microvm_build" {
  name   = "${var.prefix}-build"
  role   = aws_iam_role.microvm_build.id
  policy = data.aws_iam_policy_document.microvm_build.json
}
