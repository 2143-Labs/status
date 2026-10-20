resource "desec_rrset" "origin" {
  domain  = "2143.me"
  subname = "status-origin"
  type    = "A"
  ttl     = 3600
  rdata   = [digitalocean_reserved_ip.status.ip_address]
}
