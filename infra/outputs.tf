output "ipv4" {
  description = "Reserved IP: the address status.2143.me resolves to."
  value       = digitalocean_reserved_ip.status.ip_address
}

output "egress_ipv4" {
  description = "The droplet's own address. Probes leave from here, not from the reserved IP."
  value       = digitalocean_droplet.status.ipv4_address
}
