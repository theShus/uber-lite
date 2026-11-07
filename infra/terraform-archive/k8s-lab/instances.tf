resource "aws_key_pair" "lab" {
  key_name   = "ul-k8s-lab"
  public_key = file(pathexpand("~/.ssh/id_ed25519.pub"))
}

resource "aws_instance" "node" {
  for_each = local.nodes

  ami                         = data.aws_ami.ubuntu.id
  instance_type               = "t4g.small"
  subnet_id                   = local.net.public_subnet_ids[each.value.subnet]
  private_ip                  = each.value.ip
  associate_public_ip_address = true
  key_name                    = aws_key_pair.lab.key_name
  vpc_security_group_ids      = [aws_security_group.nodes.id]

  # cloud-init: ključ mape postaje hostname, a kubeadm ga koristi kao ime noda
  user_data = <<-EOT
    #cloud-config
    hostname: ${each.key}
    manage_etc_hosts: true
  EOT

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required" # samo IMDSv2
  }

  tags = {
    Name = "ul-${each.key}"
  }
}
