locals {
  # jedan red inventory-ja po nodu: "k8s-cp1 ansible_host=<javni IP> private_ip=10.0.0.10"
  host_line = { for name, i in aws_instance.node : name => "${name} ansible_host=${i.public_ip} private_ip=${i.private_ip}" }
}


resource "local_file" "inventory" {
  filename        = "${path.module}/../../ansible/k8s.ini"
  file_permission = "0644"
  content         = <<-EOT
    # Written by Terraform (infra/terraform/k8s-lab). Dont edit: it changes on every rebuild.
    [control_plane]
    ${local.host_line["k8s-cp1"]}

    [workers]
    ${local.host_line["k8s-w1"]}
    ${local.host_line["k8s-w2"]}

    [k8s:children]
    control_plane
    workers

    [k8s:vars]
    ansible_user=ubuntu
    ansible_ssh_common_args='-o UserKnownHostsFile=~/.ssh/known_hosts.k8s-lab -o StrictHostKeyChecking=accept-new'
  EOT
  
}

resource "local_file" "ssh_config" {
  filename        = pathexpand("~/.ssh/config.d/k8s-lab")
  file_permission = "0600"
  content = join("\n", [for name, i in aws_instance.node : <<-EOT
    Host ${name}
        HostName ${i.public_ip}
        User ubuntu
        UserKnownHostsFile ~/.ssh/known_hosts.k8s-lab
        StrictHostKeyChecking accept-new
    EOT
  ])
}
