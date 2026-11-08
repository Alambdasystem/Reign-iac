output "services" {
  description = "Only the new REIGN services. No tokens are exported."
  value = var.provision ? {
    core     = { id = render_private_service.core["pilot"].id, address = "${local.core_host}:10000" }
    temporal = { id = render_private_service.temporal["pilot"].id, address = "${local.temporal_host}:7233" }
    computer = { id = render_private_service.computer["pilot"].id, address = "${local.computer_host}:10000" }
    ax       = { id = render_web_service.ax["pilot"].id, address = render_web_service.ax["pilot"].url }
  } : {}
}
