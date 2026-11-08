variable "owner_id" {
  type    = string
  default = "tea-dbdccfpsrm7s73b8rugg"
  validation {
    condition     = var.owner_id == "tea-dbdccfpsrm7s73b8rugg"
    error_message = "This pilot is scoped to Jim's confirmed Render workspace. Review code before changing ownership."
  }
}

variable "provision" {
  type        = bool
  default     = false
  description = "Create the new pilot services only after application images and spending approval exist. Never toggle off to stop existing services."
}

variable "images" {
  description = "Audited linux/amd64 Docker releases. Keys: core, temporal, computer, ax. No latest tags."
  type = map(object({
    image_url              = string
    digest                 = string
    registry_credential_id = optional(string)
  }))
  default = {}
  validation {
    condition = !var.provision || (
      toset(keys(var.images)) == toset(["core", "temporal", "computer", "ax"]) &&
      alltrue([for image in values(var.images) : can(regex("^sha256:[0-9a-f]{64}$", image.digest)) &&
      can(regex("^[a-zA-Z0-9][a-zA-Z0-9./_-]+$", image.image_url))])
    )
    error_message = "Provide exactly four image repositories and immutable SHA256 digests before provisioning."
  }
}

variable "runtime_secrets" {
  type = object({
    core_token     = string
    computer_token = string
    ax_session_key = string
  })
  default   = { core_token = "", computer_token = "", ax_session_key = "" }
  sensitive = true
  validation {
    condition = !var.provision || (
      alltrue([for secret in values(var.runtime_secrets) : length(secret) >= 32]) &&
      length(toset(values(var.runtime_secrets))) == 3
    )
    error_message = "Three distinct random secrets (32+ characters each) are required. Use secret storage."
  }
}

variable "plans" {
  type    = object({ core = string, temporal = string, computer = string, ax = string })
  default = { core = "starter", temporal = "standard", computer = "standard", ax = "starter" }
  validation {
    condition     = alltrue([for plan in values(var.plans) : contains(["starter", "standard"], plan)])
    error_message = "The pilot permits starter or standard only; review code and costs before increasing capacity."
  }
}
