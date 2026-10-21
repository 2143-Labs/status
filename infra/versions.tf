terraform {
  required_version = ">= 1.10.0"
  required_providers {
    digitalocean = { source = "digitalocean/digitalocean", version = "~> 2.106" }
    desec        = { source = "timofurrer/desec", version = "~> 0.6.3" }
  }
  backend "s3" {
    bucket                      = "2143tf"
    key                         = "status/terraform.tfstate"
    region                      = "nyc3"
    endpoints                   = { s3 = "https://nyc3.digitaloceanspaces.com" }
    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_metadata_api_check     = true
    skip_s3_checksum            = true
    use_lockfile                = true
  }
}
