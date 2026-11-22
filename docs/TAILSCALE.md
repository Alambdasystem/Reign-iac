# Render Core to backend SSH over Tailscale

## Scope and current status

James requested this connection on 2026-11-21. The target is the existing
`reign-core-pilot-d934` private service in Jim's Render workspace. Core already
has a dedicated 1 GB disk at `/var/data/reign-vault`. This integration adds no
Render service, disk or compute-plan upgrade. It does add runtime CPU/memory
usage, which must be measured during rollout on the existing 512 MB instance.

The code remains **disabled by default**. The Blueprint temporarily keeps the
client disabled while a runtime-directory repair passes release and promotion
checks. It uses a diagnostic command that explicitly invokes the nonroot Core
entrypoint. [TAILSCALE_PROMOTION_RECEIPT.md](TAILSCALE_PROMOTION_RECEIPT.md) records
the earlier prepared rollout checkpoint. Enrollment, backend SSH and retained
identity after restart still need live verification. Keep Blueprint auto-sync off.

Core is an outbound Tailscale client. It runs neither an SSH server nor a subnet
router, exit node or publicly reachable proxy. This does not add model-call
routing, a general shell tool, a desktop adapter or GPU workload migration.

## Runtime

The Core image copies the official Tailscale `v1.104.7` binaries and installs
OpenSSH and CA certificates. The existing entrypoint prepares only the existing
vault mount and drops privileges. The image's normal CMD then invokes
`python -m reign.tailscale_runtime` before Uvicorn.

With Tailscale disabled, the wrapper directly executes the app. When enabled,
it starts a userspace `tailscaled` under the same dedicated `reign` account,
waits for authentication, then starts Core. It forwards shutdown signals and
stops Core if the daemon exits. Core's process readiness therefore requires
successful tailnet startup, but does not prove an individual SSH destination
is reachable. An enrollment failure must fail the deploy, not silently drop
the private network dependency.

State is stored in `/var/data/reign-vault/tailscale/tailscaled.state` with private
permissions. The runtime requires an actual dedicated mount and checks existing
state ownership, permissions and file types. It uses a lock to prevent two
supervisors from sharing the identity. It does not delete identity at shutdown
or repair unsafe state recursively. Keep Core at one instance with this disk.

The local control socket is `/tmp/reign-tailscale/tailscaled.sock` inside a
private directory. The app receives its path as `REIGN_TAILSCALE_SOCKET`.
When creating that directory, the runtime resets an inherited setgid group to
Core's primary group and mode `0700` through its owned directory descriptor.
Existing unsafe directories remain rejected without repair. Daemon log files
use the private persistent state directory through `TS_LOGS_DIR`; they must not
leave extra files under the ephemeral runtime directory across restarts.
No proxy port or system-wide DNS/routing change is configured. Ordinary TCP
clients use Tailscale's userspace dialer explicitly.

## Configuration

| Setting | Value or purpose |
| --- | --- |
| `REIGN_TAILSCALE_ENABLED` | `0` by default; `1` at the reviewed enrollment rollout |
| `REIGN_TAILSCALE_HOSTNAME` | `render-reign-core` |
| `REIGN_TAILSCALE_AUTHKEY` | Private one-off enrollment key; never commit its value |
| `REIGN_TAILSCALE_TAGS` | `tag:reign-render-core`; enrollment-only tag authorized in the actual tailnet |
| `REIGN_VAULT_DIR` | Existing `/var/data/reign-vault` dedicated mount |

The auth key is removed from the wrapper's environment before starting child
processes. Enrollment receives a temporary private file reference rather than
the key in command-line arguments. The file is removed after enrollment or
failure. Saved Running/Stopped identity does not consume another key on restart.
An exactly empty auth-key value means no key; whitespace remains invalid.
Tailscale child output is suppressed; storage failures expose only a fixed
message and numeric ownership, mode, type and link-count metadata.
Changing `REIGN_TAILSCALE_TAGS` does not retag an existing saved identity; manage
later tag changes explicitly in the tailnet and verify its effective policy.

Render's pre-deploy instance has no persistent disk. Its command explicitly sets
`REIGN_TAILSCALE_ENABLED=0` and removes the enrollment key with `env -u`. It also
bypasses the normal CMD runtime wrapper, so
enrollment does not depend on whether Render honors the Docker ENTRYPOINT or
when an `env` prefix takes effect. The entrypoint strips the key from any command
other than the explicit runtime invocation.

The Blueprint's `sync: false` auth-key declaration is only a placeholder.
Populate the value privately on the existing Core service and preserve all
unrelated environment variables. Do not put a key in Docker ARGs, source, chat,
CI, logs, release artifacts or a URL.

## Tailnet setup and rollout

1. Sign into the existing Tailscale account and inspect its devices and full
   access policy. Identify the actual backend OS, device identity, SSH listener,
   login user and host-key fingerprints. Do not enroll the ChatGPT workspace.
2. Add a service identity/tag with access only to the intended backend SSH
   destinations on TCP 22 (or an explicitly verified custom port). Review
   existing grants/ACLs: they are additive, so a narrow new grant does not remove
   an existing allow-all rule. Preserve unrelated policy and user access.
3. Generate a short-lived, one-off, non-ephemeral key carrying the approved
   service tag. Preauthorize only if appropriate under the existing device
   approval policy. Store it privately on the existing Core service. Keep the
   selected hostname stable. Device/key approval remains a tailnet operation.
4. Run the repository's required validation. The container check reproduces
   setgid inheritance with primary UID/GID 10001 and supplementary group 1000.
   It starts the actual pinned daemon twice with Docker networking disabled,
   checks private log placement and retained CLI preferences, and never enrolls
   a device. The release workflow repeats this check on the exact image it
   publishes. These checks do not establish internet reachability,
   authentication, retained node identity or backend SSH.
5. Publish the tested Core image through the existing release workflow. Promote
   its immutable digest through the existing Blueprint
   `exs-dbofqkm417fc7384kou0` and set `REIGN_TAILSCALE_ENABLED=1` only when the
   key and tailnet policy are ready. Keep auto-sync off. Preserve the other
   three service image pins, plans, disks, keys and application settings.
6. Expect an interruption while Core's persistent disk moves to the new
   instance. Watch deployment logs, Core memory/CPU and application readiness.
   Record actual enrollment, SSH and application evidence separately.
7. After successful enrollment, remove the one-off auth key from the service
   configuration, or set it to an exactly empty value, without replacing
   unrelated variables. Test restart with
   the retained identity and no enrollment key. Do not revoke the node itself.

## SSH verification from the deployed service

Run diagnostics as the `reign` account (or an authorized root administrator)
in the deployed Core container. The private socket is not reachable from other
Render services or from this coding workspace.
Storage-validator diagnostics must run as the dedicated service account. A
temporary Render Docker command must explicitly invoke `python -m
reign.core_entrypoint` before its diagnostic command; the live custom-command
path bypassed the image entrypoint. Remove the override when promoting the
repaired image so normal startup uses its image entrypoint and CMD.

```bash
/usr/local/bin/tailscale --socket=/tmp/reign-tailscale/tailscaled.sock status
/usr/local/bin/tailscale --socket=/tmp/reign-tailscale/tailscaled.sock ping BACKEND_NAME
```

For an ordinary OpenSSH server, configure a private backend SSH key and a
known-hosts file containing independently verified host keys, then use:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 \
  -o 'ProxyCommand=/usr/local/bin/tailscale --socket=/tmp/reign-tailscale/tailscaled.sock nc %h %p' \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/etc/secrets/backend-known-hosts \
  -i /etc/secrets/backend-ssh-key \
  BACKEND_USER@BACKEND_NAME hostname
```

Replace placeholders only after verifying the actual backend. Provision SSH
credentials privately; Tailscale membership does not supply an OS login or
start a missing SSH server. In particular, Windows needs a running OpenSSH
server and its own account authorization. Do not disable host-key checking.
The image adds the service account to Render's secret-file group 1000. Verify
the actual private key's ownership/readability and SSH's permission checks
during rollout; if a private runtime copy is needed, restrict it to the service
account with mode 0600 and never include it in the image.

For a backend explicitly configured to accept **Tailscale SSH**, the alternative
is `tailscale --socket=/tmp/reign-tailscale/tailscaled.sock ssh USER@HOST hostname`.
That requires both network access and the tailnet's SSH policy. Tailscale SSH
server support and tagged-source restrictions differ from ordinary OpenSSH;
do not substitute it automatically or retag user-owned PCs without review.

Acceptance requires: the intended node appears online in the intended tailnet;
a read-only SSH command returns the expected backend identity; Core and its
authenticated application dependencies are healthy; and a restart preserves
the same Tailscale identity without another auth key. A local process test,
public AX health endpoint or successful `tailscale ping` alone is insufficient.

## Rollback

Set `REIGN_TAILSCALE_ENABLED=0` in the existing Blueprint and redeploy the tested
image, or restore the prior Core digest through the same owner. Preserve the
disk and unrelated secrets. Disabling the client does not remove its tailnet
device or policy; remove only the new service identity/grants separately if
that rollback is requested. Do not delete the vault or its other contents.

## Primary references

- [Tailscale userspace networking](https://tailscale.com/docs/concepts/userspace-networking)
- [Tailscale container parameters](https://tailscale.com/docs/features/containers/docker/docker-params)
- [Tailscale CLI](https://tailscale.com/docs/reference/tailscale-cli)
- [Tailscale SSH](https://tailscale.com/docs/features/tailscale-ssh)
- [Tailscale v1.104.7](https://github.com/tailscale/tailscale/releases/tag/v1.104.7)
- [Render deployment lifecycle](https://render.com/docs/deploys)
- [Render environment variables and secret files](https://render.com/docs/configure-environment-variables)
