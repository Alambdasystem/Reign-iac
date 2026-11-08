locals {
  pilot = var.provision ? toset(["pilot"]) : toset([])
  # Use explicit main ports: a private-service URL can include a different port.
  temporal_host = try(split(":", render_private_service.temporal["pilot"].url)[0], "")
  computer_host = try(split(":", render_private_service.computer["pilot"].url)[0], "")
  core_host     = try(split(":", render_private_service.core["pilot"].url)[0], "")
}

resource "render_private_service" "temporal" {
  for_each       = local.pilot
  name           = "reign-temporal-pilot"
  plan           = var.plans.temporal
  region         = "oregon"
  num_instances  = 1
  runtime_source = { image = var.images["temporal"] }
  disk           = { name = "reign-temporal-data", size_gb = 1, mount_path = "/data" }
  env_vars       = { PORT = { value = "7233" } }
  previews       = { generation = "off" }
  lifecycle { prevent_destroy = true }
}

resource "render_private_service" "computer" {
  for_each       = local.pilot
  name           = "reign-computer-pilot"
  plan           = var.plans.computer
  region         = "oregon"
  num_instances  = 1
  runtime_source = { image = var.images["computer"] }
  env_vars = {
    PORT                = { value = "10000" }
    TEMPORAL_ADDRESS    = { value = "${local.temporal_host}:7233" }
    TEMPORAL_NAMESPACE  = { value = "default" }
    TEMPORAL_TASK_QUEUE = { value = "reign-computer" }
    COMPUTER_TOKEN      = { value = var.runtime_secrets.computer_token }
    ALLOWED_HOSTS       = { value = "alambda-forge.onrender.com" }
    MAX_SESSIONS        = { value = "1" }
  }
  previews = { generation = "off" }
  lifecycle { prevent_destroy = true }
}

resource "render_private_service" "core" {
  for_each       = local.pilot
  name           = "reign-core-pilot"
  plan           = var.plans.core
  region         = "oregon"
  num_instances  = 1
  runtime_source = { image = var.images["core"] }
  env_vars = {
    PORT                = { value = "10000" }
    TEMPORAL_ADDRESS    = { value = "${local.temporal_host}:7233" }
    TEMPORAL_NAMESPACE  = { value = "default" }
    TEMPORAL_TASK_QUEUE = { value = "reign-computer" }
    COMPUTER_URL        = { value = "http://${local.computer_host}:10000" }
    COMPUTER_TOKEN      = { value = var.runtime_secrets.computer_token }
    CORE_TOKEN          = { value = var.runtime_secrets.core_token }
    ALLOWED_HOSTS       = { value = "alambda-forge.onrender.com" }
  }
  previews = { generation = "off" }
  lifecycle { prevent_destroy = true }
}

resource "render_web_service" "ax" {
  for_each          = local.pilot
  name              = "reign-ax-pilot"
  plan              = var.plans.ax
  region            = "oregon"
  num_instances     = 1
  runtime_source    = { image = var.images["ax"] }
  health_check_path = "/healthz"
  env_vars = {
    PORT           = { value = "10000" }
    CORE_URL       = { value = "http://${local.core_host}:10000" }
    CORE_TOKEN     = { value = var.runtime_secrets.core_token }
    AX_SESSION_KEY = { value = var.runtime_secrets.ax_session_key }
  }
  previews = { generation = "off" }
  lifecycle { prevent_destroy = true }
}
