# status.2143.me -> the droplet's reserved IP. One resource, because changing
# `type` forces a replacement: OpenTofu deletes the CNAME before creating the A,
# which is the only order deSEC accepts.
resource "desec_rrset" "status" {
  domain  = "2143.me"
  subname = "status"
  type    = "A"
  ttl     = 3600
  rdata   = [digitalocean_reserved_ip.status.ip_address]
}
