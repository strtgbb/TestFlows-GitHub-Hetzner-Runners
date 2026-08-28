# AGENTS.md — TestFlows GitHub Runners

Reference for AI agents working in this codebase. Covers architecture,
conventions, and the design decisions that aren't obvious from the code.

Two more AGENTS.md files add local detail:
`testflows/github/runners/AGENTS.md` (core) and
`testflows/github/runners/config/AGENTS.md` (config).

---

## What This Project Does

`tfs-github-runners` is a self-hosted GitHub Actions autoscaler. It watches a
GitHub repository for queued workflow jobs, provisions cloud VMs to run them as
ephemeral runners, and cleans them up when they go idle. It runs continuously,
usually as a systemd unit or on a small cloud VM.

The project started as Hetzner-only and still carries that name in places (the
repo URL, the legacy `testflows.github.hetzner.runners` distribution). The
package is now multi-provider: Hetzner, Scaleway, AWS, and static dedicated
hosts.

---

## Source Layout

All source lives under `testflows/github/runners/`. Do not look for
`testflows/github/hetzner/runners/` — that path is gone.

| Path | Role |
|---|---|
| `bin/tfs-github-runners` | CLI entry point, argument parser, and service main loop |
| `scale_up.py` | Job detection → VM provisioning → runner registration (largest file) |
| `scale_down.py` | Idle runner detection → power-off, recycle, or delete |
| `api_watch.py` | Polls the GitHub API for queued jobs; publishes to the mailbox |
| `cloud_provider.py` | `CloudProvider` abstract base plus the shared provider dataclasses |
| `providers/` | One package per provider: `hetzner`, `scaleway`, `aws`, `dedicated_static` |
| `config_schema.py` | Config dataclasses only — a leaf module with no package imports |
| `config/` | YAML parsing, env expansion, validation, CLI merge, provider factory |
| `constants.py` | Server and runner name prefixes, label names |
| `recycling.py` | Shared mechanics for providers with a stopped-server recycle pool |
| `provider_hooks.py` | Dispatches the orchestration hooks across configured providers |
| `servers.py`, `volumes.py` | CLI subcommands for listing and deleting resources |
| `estimate.py` | Cost estimation |
| `metrics.py` | Prometheus metrics |
| `logger.py` | Structured CSV logging with context-aware field injection |
| `actions.py` | `Action` context manager for structured operation logging |
| `cloud.py`, `service.py` | Deploy the runner service itself to a cloud VM or systemd |
| `dashboard/` | Streamlit monitoring dashboard (`panels/`, `metrics/`) |
| `scripts/` | Runner setup, startup, and recycle shell scripts; `scripts/deploy/` for the service |
| `tests/unit/` | TestFlows unit suite — see `tests/unit/README.md` |

---

## Runtime Architecture

The service submits three long-running tasks to a `ThreadPoolExecutor`:

```
api_watch()    — polls GitHub for queued jobs; publishes to the mailbox queue
scale_up()     — consumes the mailbox; provisions VMs; registers runners
scale_down()   — independently powers off, recycles, and deletes servers
```

The mailbox is a thread-safe queue. `scale_up` also uses the worker pool for
concurrent server creation. All VM setup happens over SSH after creation.

`scale_up` is stateless by design: every pass rebuilds its picture from the
provider APIs and GitHub. Do not add cooldown dicts, dedup caches, or other
cross-pass memory to it — if a job is being handled twice, the cause is in
label or config resolution.

---

## Label System

Jobs declare what they need through GitHub runner labels, parsed at dispatch
time in `scale_up.py`.

| Label | Meaning |
|---|---|
| `type-{name}` | Server type, e.g. `type-cx23`. Multiple allowed — see fallback below |
| `in-{name}` | Location, mapped to each provider's native concept |
| `image-{arch}-{kind}-{name}` | Image, e.g. `image-x86-system-ubuntu-22.04` |
| `disk-{N}` | Minimum root disk size in GB, e.g. `disk-100` or `disk-100GB` |
| `volume-{...}` | Attach a separate cache block volume (Hetzner only) |
| `provider-{name}` | Pin the job to one configured provider, e.g. `provider-aws` |
| `net-ipv4`, `net-ipv6` | Restrict public networking. Neither label means both |
| `setup-{name}` | Run `{name}.sh` from `--scripts` during server setup |
| `startup-{name}` | Run `{name}.sh` from `--scripts` on each runner start |
| `recycle-{name}` | Run `{name}.sh` instead of `recycle.sh` when reactivating a server |

`disk-` and `volume-` are different things. `disk-` sizes the server's own root
disk and is a **minimum** — resizable providers (AWS, Scaleway SBS) provision it
directly, fixed-disk providers (Hetzner, Scaleway local-boot) are checked
against it. `volume-` attaches a separate cache volume.

`get_server_types()` returns every `type-` label as a list, and the
provisioning loop tries them in order. That's the fallback mechanism. A type
name containing `-` is skipped — it's treated as a composite label fragment,
not a type name. This is why Scaleway types are configured in dot-form
(`dev1.s`), not Scaleway's native `DEV1-S`.

`in-` maps to whatever the provider calls a location: a Hetzner DC (`nbg1`), an
AWS availability zone (`us-east-1a`), a Scaleway zone (`fr-par-1`). AWS uses AZ
granularity rather than region because EBS volumes are AZ-scoped.

### Meta-Labels

One label can expand to a full set through config:

```yaml
meta_label:
  test-arm: [self-hosted, type-cax21, image-arm-system-ubuntu-22.04]
```

A meta-label always includes itself in the expansion. `expand_meta_label()` in
`scale_up.py` handles this. Meta-labels are also the multi-provider mechanism
for jobs — one can expand to `type-` labels from different providers, and the
existing fallback loop tries each. No new label syntax, no workflow changes.

### Label Prefix

Set a prefix so several controllers can share a repository. With prefix
`team-a`, only labels starting with `team-a-` are recognized (`team-a-type-cx23`).

---

## Providers

Every provider subclasses `CloudProvider` in `cloud_provider.py` and lives in
its own package under `providers/`, each with the same file names: `config.py`
(dataclass helpers), `args.py` (CLI arguments), `provider.py` (the
implementation), `estimate.py` (pricing), `utils.py`.

`config/factory.py` builds the provider list from config. Provider selection is
implicit: when a job asks for `type-cx23`, each configured provider is asked
whether it offers that type, first match wins. Provider type namespaces are
vendor-specific in practice, so collisions are rare. A `provider-` label pins
the choice when it matters.

The field order of `provider_list` in `config_schema.py` is the precedence used
when a job carries no `type-`/`in-` label: hetzner, scaleway, aws,
dedicated_static. Scaleway sits ahead of AWS deliberately.

`CloudProvider` already carries a large recycling surface (`claim_recycled_server`
and friends) to support Scaleway's SBS mode. Resist adding more
provider-specific methods to the base class.

---

## Configuration

Precedence, highest first: CLI arguments → project config (`--project`, see
`projects.py`) → environment variables (`$GITHUB_TOKEN`, `$GITHUB_REPOSITORY`,
`$RUNNERS_CONFIG`) → config file → dataclass defaults. The default config file
is `~/.tfs-runners/config.yaml`. YAML values support `${ENV_VAR}` expansion.

Credentials live under `providers:`:

```yaml
providers:
  hetzner:
    token: ${HETZNER_TOKEN}
  aws:
    access_key_id: ${AWS_ACCESS_KEY_ID}
    secret_access_key: ${AWS_SECRET_ACCESS_KEY}
```

There is no top-level token field. `Config.hetzner_token` is a read-only
property derived from `providers.hetzner.token`, kept for the Hetzner-specific
internal readers. Startup fails if no provider is configured.

### Import Layering

The config package and the providers layer used to import each other. Two rules
now keep that cycle broken:

- Nothing in `providers/` may import the `config` package or `args`.
- The leaf modules — `config_schema.py`, `argtypes.py`, `cloud_provider.py` —
  may not import them either.

`tests/unit/features/import_layering.py` is a static guard that fails if
anything reintroduces a forbidden edge. In-function imports are exempt.

---

## Testing

The unit suite is TestFlows-based and lives in
`testflows/github/runners/tests/unit/`. All cloud, SSH, and GitHub I/O is
mocked, so it runs offline in a few seconds. See `tests/unit/README.md` for how
to run it and how to add a test.

Verify changes by adding scenarios to that suite, not with throwaway scripts.
A new feature file needs a line in `regression.py` to run.

---

## Conventions

- Dataclasses for domain objects and config.
- Wrap significant operations in the `Action` context manager for structured logs.
- Retry cloud calls with exponential backoff (`request.py`).
- Emit metrics for new cloud operations (`metrics.py`).
- Volume operations are guarded by `threading.Lock()`.
- Don't handle errors that can't happen. Validate at system boundaries only.
- Don't add speculative abstractions. Build what the task needs.
- Comments explain *why*, not *what*, and never reference repo history — no
  commit hashes, PR numbers, or "changed in X". That belongs in git.
