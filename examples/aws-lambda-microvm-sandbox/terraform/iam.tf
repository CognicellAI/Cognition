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

data "aws_iam_policy_document" "network_connector_assume_role" {
  statement {
    actions = [
      "sts:AssumeRole",
      "sts:TagSession",
    ]

    principals {
      type        = "Service"
      identifiers = ["network-connectors.lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "default_agent_execution" {
  name               = "${var.prefix}-agent-execution"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "default_agent_execution" {
  statement {
    sid = "CreateRuntimeLogGroups"

    actions = [
      "logs:CreateLogGroup",
    ]

    resources = [
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda-microvms/${var.prefix}*",
    ]
  }

  statement {
    sid = "WriteRuntimeLogStreams"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda-microvms/${var.prefix}*:log-stream:*",
    ]
  }
}

resource "aws_iam_role_policy" "default_agent_execution" {
  name   = "${var.prefix}-agent-execution"
  role   = aws_iam_role.default_agent_execution.id
  policy = data.aws_iam_policy_document.default_agent_execution.json
}

resource "aws_iam_role" "network_connector_operator" {
  count = var.create_vpc_egress_connector ? 1 : 0

  name               = "${var.prefix}-network-connector"
  assume_role_policy = data.aws_iam_policy_document.network_connector_assume_role.json
}

data "aws_iam_policy_document" "network_connector_operator" {
  count = var.create_vpc_egress_connector ? 1 : 0

  statement {
    sid = "ManageNetworkConnectorEnis"

    actions = [
      "ec2:CreateNetworkInterface",
    ]

    resources = concat(
      ["arn:aws:ec2:${var.aws_region}:${local.account_id}:network-interface/*"],
      [for subnet_id in var.private_subnet_ids : "arn:aws:ec2:${var.aws_region}:${local.account_id}:subnet/${subnet_id}"],
      [
        "arn:aws:ec2:${var.aws_region}:${local.account_id}:security-group/${aws_security_group.microvm_vpc_egress[0].id}",
      ],
    )
  }

  statement {
    sid = "InspectNetworkConnectorDependencies"

    actions = [
      "ec2:DeleteNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSubnets",
      "ec2:DescribeVpcs",
    ]

    resources = ["*"]
  }

  statement {
    sid = "TagManagedNetworkConnectorEnis"

    actions   = ["ec2:CreateTags"]
    resources = ["arn:aws:ec2:${var.aws_region}:${local.account_id}:network-interface/*"]

    condition {
      test     = "StringEquals"
      variable = "ec2:ManagedResourceOperator"
      values   = ["network-connectors.lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "network_connector_operator" {
  count = var.create_vpc_egress_connector ? 1 : 0

  name   = "${var.prefix}-network-connector"
  role   = aws_iam_role.network_connector_operator[0].id
  policy = data.aws_iam_policy_document.network_connector_operator[0].json
}

data "aws_iam_policy_document" "control_plane" {
  statement {
    sid = "ManageApprovedMicrovmLifecycle"

    actions = concat(
      [
        "lambda:GetMicrovm",
        "lambda:ListMicrovms",
        "lambda:ResumeMicrovm",
        "lambda:RunMicrovm",
        "lambda:SuspendMicrovm",
        "lambda:TerminateMicrovm",
      ],
      local.control_plane_token_actions,
    )

    resources = [
      var.prebuilt_microvm_image_arn,
      "arn:aws:lambda:${var.aws_region}:${local.account_id}:microvm:*",
    ]
  }

  statement {
    sid = "UseApprovedNetworkConnectors"

    actions = ["lambda:PassNetworkConnector"]

    resources = concat(
      [
        local.aws_managed_all_ingress_connector_arn,
        local.aws_managed_internet_egress_connector_arn,
      ],
      local.vpc_egress_connector_arns,
    )
  }

  statement {
    sid = "PassApprovedAgentExecutionRoles"

    actions   = ["iam:PassRole"]
    resources = local.execution_role_arns

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "control_plane" {
  name        = "${var.prefix}-control-plane"
  description = "Allows Cognition to run approved Lambda MicroVM sandboxes."
  policy      = data.aws_iam_policy_document.control_plane.json
}

data "aws_iam_role" "control_plane" {
  for_each = toset(var.control_plane_role_names)

  name = each.value
}

resource "aws_iam_role_policy_attachment" "control_plane" {
  for_each = data.aws_iam_role.control_plane

  role       = each.value.name
  policy_arn = aws_iam_policy.control_plane.arn
}
