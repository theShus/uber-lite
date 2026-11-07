output "cp1_public_ip" {
  value = aws_instance.node["k8s-cp1"].public_ip
}

output "nodes" {
  value = { for name, i in aws_instance.node : name => { public = i.public_ip, private = i.private_ip, az = i.availability_zone } }
}
