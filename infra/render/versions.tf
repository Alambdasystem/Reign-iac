terraform {
  required_version = "= 1.13.0"
  required_providers {
    render = {
      source  = "render-oss/render"
      version = "= 1.9.1"
    }
  }
  # Supply TF_HTTP_* through the protected execution environment, never in Git.
  # The selected server MUST implement locking and encrypt storage/backups.
  backend "http" {}
}

provider "render" {
  owner_id                   = var.owner_id
  wait_for_deploy_completion = true
}
