resource "aws_security_group" "nodes" {
  name        = "ul-k8s-lab-nodes"
  description = "kubeadm lab nodes"
  vpc_id      = local.net.vpc_id

  tags = {
    Name = "ul-k8s-lab-nodes"
  }
}

# nodovi medjusobno: izvor je ovaj isti SG
resource "aws_vpc_security_group_ingress_rule" "between_nodes" {
  security_group_id            = aws_security_group.nodes.id
  referenced_security_group_id = aws_security_group.nodes.id
  ip_protocol                  = "-1"
  description                  = "Everything between cluster nodes (lab choice)"
}

resource "aws_vpc_security_group_ingress_rule" "ssh_from_home" {
  security_group_id = aws_security_group.nodes.id
  cidr_ipv4         = var.my_ip_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  description       = "SSH from the hub (Ansible)"
}

resource "aws_vpc_security_group_ingress_rule" "api_from_home" {
  security_group_id = aws_security_group.nodes.id
  cidr_ipv4         = var.my_ip_cidr
  ip_protocol       = "tcp"
  from_port         = 6443
  to_port           = 6443
  description       = "kubectl from the hub"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.nodes.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "Packages and images"
}
