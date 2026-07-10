resource "aws_security_group" "microvm_vpc_egress" {
  count = var.create_vpc_egress_connector ? 1 : 0

  name        = "${var.prefix}-microvm-egress"
  description = "Lambda MicroVM VPC egress connector security group"
  vpc_id      = var.vpc_id

  lifecycle {
    precondition {
      condition = (
        var.vpc_id != null
        && length(var.private_subnet_ids) > 0
        && length(var.vpc_egress_cidr_blocks) > 0
      )
      error_message = "Set vpc_id, private_subnet_ids, and vpc_egress_cidr_blocks when create_vpc_egress_connector is true."
    }
  }
}

resource "aws_vpc_security_group_egress_rule" "microvm_vpc_egress" {
  for_each = var.create_vpc_egress_connector ? toset(var.vpc_egress_cidr_blocks) : toset([])

  security_group_id = aws_security_group.microvm_vpc_egress[0].id
  cidr_ipv4         = each.value
  ip_protocol       = "-1"
}

resource "awscc_lambda_network_connector" "vpc_egress" {
  count = var.create_vpc_egress_connector ? 1 : 0

  name          = "${var.prefix}-vpc-egress"
  operator_role = aws_iam_role.network_connector_operator[0].arn

  configuration = {
    vpc_egress_configuration = {
      associated_compute_resource_types = ["MicroVm"]
      network_protocol                  = "IPv4"
      security_group_ids                = [aws_security_group.microvm_vpc_egress[0].id]
      subnet_ids                        = var.private_subnet_ids
    }
  }

  tags = local.awscc_tags

  depends_on = [
    aws_iam_role_policy.network_connector_operator,
  ]
}
