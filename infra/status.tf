# status.2143.me. Adopted as-is (a CNAME to the home edge) so the next change can
# repoint it at the droplet in one apply. See docs/runbook.md "Cutover".
import {
  to = desec_rrset.status
  id = "2143.me/status/CNAME"
}

resource "desec_rrset" "status" {
  domain  = "2143.me"
  subname = "status"
  type    = "CNAME"
  ttl     = 3600
  rdata   = ["john2143.com."]
}
