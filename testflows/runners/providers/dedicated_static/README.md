# `dedicated_static` provider

A `CloudProvider` implementation for **long-lived, pre-provisioned hosts** (bare metal,
on-prem VMs, anything reachable over SSH) listed in YAML. Unlike the cloud providers it
**creates and deletes no infrastructure**: a host always physically exists, so the provider's
job is to *lease* an idle host to a runner and *unlease* it afterwards.

If you're used to the Hetzner/AWS providers, the points below are where this one behaves
differently — read them before changing anything here.

## How it differs from the cloud providers

| Concern | Cloud (Hetzner/AWS) | `dedicated_static` |
|---|---|---|
| `create_server` | provisions a new VM | **leases an idle configured host** (returns `None` if none claimable) |
| `delete_server` | destroys the VM | **unleases the host** (it keeps running) |
| Existence | a server exists ⇔ it's allocated | host **always exists**; allocation is tracked separately (see lease model) |
| Setup step | `setup.sh` (provisioning) | **`recycle.sh` (cleanup)** — see "Setup step" |
| Setup-step script label | `setup-<name>` | `recycle-<name>` (**`setup-` ignored**) |
| `supports_recycling` | Hetzner `True` | **`False`** — never enters the power-off/rename/rebuild parking loop |
| `power_off`/`power_on`/`rebuild` | implemented | **`NotImplementedError`** (can't power-cycle someone else's hardware) |
| Runner name | `{name}-{type}-{location}` | **bare stable `static_name`** — see "Naming" |
| Images | AMI / hcloud image | **none** — `image-` labels are silently ignored (the lease is fixed hardware) |
| SSH keys | uploaded via API | **`get_or_create_ssh_key` just hashes the key** (no upload); SSH auth uses the configured key path |
| Lifecycle | scale_up creates, scale_down destroys | runners are `--ephemeral` + `runner_on_exit="reboot"`: a finished host self-deregisters and reboots back to idle |

## Configuration

Hosts are declared under `config.providers.dedicated_static`, one lease slot per host endpoint.
Type/location routing works the same way as for every provider (`type-`/`in-` labels under the
configured `label_prefix`); only the static-specific validation rules are called out below.

```yaml
config:
  providers:
    dedicated_static:
      ssh_defaults:
        user: runner
        key: /path/to/id_ed25519
        port: 22
      groups:
        metal-dc1:                   # group name must match ^[a-z0-9][a-z0-9-]*$
          labels:
            - tfs-self-hosted
            - tfs-type-metallarge    # >=1 type- label required; type name may NOT contain "-"
            - tfs-in-dc1             # optional location (in- label)
          hosts:                     # one lease slot per endpoint
            - 203.0.113.10
            - 203.0.113.11
          # optional per-group SSH overrides: ssh_user / ssh_port / ssh_key_path
```

**Custom startup scripts.** The provider passes `RUNNER_ON_EXIT=reboot` to the startup script so
a finished host reboots back into the idle pool. The stock default is `poweroff`, which would
strand a static host — the provider can't power it back on (see the table). A custom
`startup-<name>` script must honour the `RUNNER_ON_EXIT` env var rather than hard-coding shutdown.

**Ignored config.** Static hosts are reused in place, never parked, so the cloud recycle/parking
options have no effect here: `recycle`, `recycle_without_rebuild`, and `recycle_grace_period` are
all ignored.

## Lease model

Because a host always exists, "is this host in use?" can't be answered by existence. Two layers
answer it:

1. **In-memory lease cache** (`_StaticHost.lease_name`) — re-derived every cycle from the live
   GitHub runner list by `reconcile_runner_leases`: a host whose `static_name` is a registered
   runner is leased; everything else is cleared. This is a **cache, not the source of truth** —
   it does not survive a restart and is rebuilt from GitHub each cycle.
2. **Durable claim marker** (`/run/user/$UID/testflows-github-runners/claim`) — covers the gap the cache can't: the
   *in-flight setup window* before a runner has registered. `create_server` takes an optimistic
   in-memory lease, then (outside the lock) **atomically `mkdir`s** the marker — one racer wins,
   others get `EEXIST`, so it's safe across controller processes and restarts. A marker older
   than `claim_ttl_minutes` (default 360 minutes) is treated as a crashed/abandoned setup and
   reclaimed. On successful setup the claim is kept (reboot-scoped behavior); on setup failure
   `release_claim` clears it and also frees the in-memory lease.

   This is how a freshly-leased host still in setup isn't double-dispatched to a second queued
   job before its runner has registered.

## Setup step runs `recycle.sh`

The "setup step" is the script run on a host just before its runner registers. On cloud
providers it provisions a fresh VM (`setup.sh`). Here the host is already provisioned out of
band, so there is nothing to provision — the step instead **cleans up prior-job state** by
running `recycle.sh` (or a `recycle-<name>` override; see the label row above).

The swap is lossless: `recycle.sh` only wipes `~/_work`, docker state, and stale runner metadata,
and it is the *startup* script — not the setup step — that downloads and registers the runner.

## Naming

`build_runner_name` returns the host's **bare, stable** `static_name`:

```
{runner_name_prefix}static-{group}-{md5(endpoint)[:12]}
```

It has **no** `-{type}-{location}` suffix (cloud names do). The runner registers under this name
unchanged, which is also why `server.get_runner_server_name` has a `static-` prefix guard that
returns the name as-is instead of truncating it.

## See also

- `testflows/runners/tests/features/dedicated_static_provider.py` — claim-marker / lease tests
  (all SSH mocked at the `ssh` boundary).
