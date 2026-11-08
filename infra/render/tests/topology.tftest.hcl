mock_provider "render" {
  mock_resource "render_private_service" {
    defaults = { url = "mock-private:10000", id = "srv-mocked" }
  }
  mock_resource "render_web_service" {
    defaults = { url = "https://mock-ax.onrender.com", id = "srv-mocked-web" }
  }
}

run "disabled_by_default" {
  command = plan
  assert {
    condition     = length(output.services) == 0
    error_message = "Default configuration must not provision services."
  }
}

run "reject_missing_release_inputs" {
  command = plan
  variables { provision = true }
  expect_failures = [var.images, var.runtime_secrets, var.ax_public_origin]
}

run "four_isolated_services" {
  # Plan-only avoids test teardown conflicting with prevent_destroy.
  command = plan
  variables {
    provision        = true
    ax_public_origin = "https://reign-ax-pilot.onrender.com"
    # Synthetic values for mocked provider tests only; not usable credentials.
    runtime_secrets = {
      core_token        = "test-only-core-value-not-a-secret-000"
      computer_token    = "test-only-computer-value-not-secret-0"
      ax_session_key    = "test-only-ax-value-not-a-secret-0000"
      ax_admin_password = "test-only-admin-value-not-secret-000"
    }
    images = {
      core     = { image_url = "example.invalid/reign/core", digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }
      temporal = { image_url = "example.invalid/reign/temporal", digest = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" }
      computer = { image_url = "example.invalid/reign/computer", digest = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc" }
      ax       = { image_url = "example.invalid/reign/ax", digest = "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd" }
    }
  }
  assert {
    condition     = length(output.services) == 4
    error_message = "All four independent services must be present."
  }
  assert {
    condition     = render_private_service.temporal["pilot"].disk.mount_path == "/data" && render_private_service.temporal["pilot"].num_instances == 1
    error_message = "The pilot Temporal store must be persistent and single-instance."
  }
  assert {
    condition     = render_private_service.computer["pilot"].env_vars.ALLOWED_HOSTS.value == "alambda-forge.onrender.com"
    error_message = "Computer must stay restricted to Git High."
  }
  assert {
    condition     = render_web_service.ax["pilot"].health_check_path == "/healthz"
    error_message = "AX must expose a Render HTTP health check."
  }
}
