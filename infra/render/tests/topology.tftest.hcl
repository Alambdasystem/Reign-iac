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
  expect_failures = [var.images, var.runtime_secrets]
}

run "four_isolated_services" {
  command = apply
  variables {
    provision = true
    # Synthetic values for mocked provider tests only; not usable credentials.
    runtime_secrets = {
      core_token     = "test-only-core-value-not-a-secret-000"
      computer_token = "test-only-computer-value-not-secret-0"
      ax_session_key = "test-only-ax-value-not-a-secret-0000"
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
    condition     = render_private_service.core["pilot"].env_vars.TEMPORAL_ADDRESS.value == "mock-private:7233"
    error_message = "Temporal must use its gRPC port, not the HTTP port."
  }
  assert {
    condition     = render_web_service.ax["pilot"].env_vars.CORE_URL.value == "http://mock-private:10000"
    error_message = "AX must connect to Core through the private network."
  }
}
