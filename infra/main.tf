provider "digitalocean" {}
provider "desec" {}

variable "region" {
  type    = string
  default = "tor1"
}

variable "droplet_size" {
  type    = string
  default = "s-1vcpu-512mb-10gb"
}

variable "ssh_key_name" {
  type    = string
  default = "k3s-cloud"
}

data "digitalocean_ssh_key" "admin" {
  name = var.ssh_key_name
}

resource "digitalocean_reserved_ip" "status" {
  region = var.region
}

resource "digitalocean_droplet" "status" {
  name       = "status-1"
  region     = var.region
  size       = var.droplet_size
  image      = "ubuntu-24-04-x64"
  ipv6       = false
  monitoring = true
  backups    = true
  tags       = ["status"]
  ssh_keys   = [data.digitalocean_ssh_key.admin.fingerprint]

  backup_policy {
    plan    = "weekly"
    weekday = "SUN"
    hour    = 8
  }

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    repo_url = "https://github.com/2143-Labs/status.git"
  })

  lifecycle {
    ignore_changes = [user_data, image]
  }
}

resource "digitalocean_reserved_ip_assignment" "status" {
  ip_address = digitalocean_reserved_ip.status.ip_address
  droplet_id = digitalocean_droplet.status.id
}

resource "digitalocean_firewall" "status" {
  name        = "status-1"
  droplet_ids = [digitalocean_droplet.status.id]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "80"
    source_addresses = ["0.0.0.0/0"]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "443"
    source_addresses = ["0.0.0.0/0"]
  }

  inbound_rule {
    protocol         = "icmp"
    source_addresses = ["0.0.0.0/0"]
  }

  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0"]
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0"]
  }
}
