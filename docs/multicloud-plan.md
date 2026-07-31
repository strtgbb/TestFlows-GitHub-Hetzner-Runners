# Multi-Cloud Support — Plan

Live planning document for the multi-cloud refactor. Update as decisions are made and phases complete.

---

## Goal

Extend the runner service to support multiple cloud providers beyond Hetzner. AWS is the first target. The design should leave the door open for GCP and others without requiring upfront implementation.

---

## Design Decisions

### Provider selection is implicit

Jobs do not specify a provider in their labels. When `scale_up` encounters `type-cx23`, it queries each configured provider to find one that offers that type. Vendor type namespaces are distinct in practice (Hetzner: `cx`/`cax`/`ccx`; AWS: `t3`/`m5`/`c5`/`t4g` etc.), so collisions are unlikely and acceptable.

Explicit targeting is also available: a `provider-<name>` label pins a job to one configured provider. (Added — was originally deferred as "later if needed".)

### Meta-labels are the multi-provider job interface

Job authors remain provider-agnostic. The config owner maps logical names to provider-specific hardware:

```yaml
meta_label:
  standard-linux: [type-cx23, type-t3medium]
```

The existing fallback loop in `scale_up` (which already iterates through all matched server types) handles trying each option. No changes to job workflow files, no new label syntax.

### New files for the abstraction layer

- `cloud_provider.py` — abstract `CloudProvider` base class
- `providers/hetzner.py` — Hetzner implementation (refactored from existing code)
- `providers/aws.py` — AWS implementation

### Config structure

Everything for a provider lives under `providers.<name>`. **No backwards
compatibility** — this branch is a pre-release spin-off, so the old flat format
was deleted outright (decision reversed from the original "preserve during
transition"). Top-level `hetzner_token` and top-level `default_image` /
`default_server_type` / `default_location` / `default_volume_size` /
`default_volume_location` are gone and now **hard-error** on parse; use
`providers.hetzner.token` and `providers.hetzner.defaults`.

Global settings act as defaults with per-provider overrides for `max_runners`,
`end_of_life`, `recycle`, `recycle_grace_period`. Provider precedence is
declaration order (`hetzner → scaleway → aws → dedicated_static`); the first
configured provider seeds unlabeled jobs.

```yaml
providers:
  hetzner:
    token: ${HETZNER_TOKEN}
    defaults: { image: "x86:system:ubuntu-22.04", server_type: cx23, location: nbg1, volume_size: 10 }
  scaleway:
    access_key: ${SCW_ACCESS_KEY}
    secret_key: ${SCW_SECRET_KEY}
    project_id: ${SCW_DEFAULT_PROJECT_ID}
    defaults: { image: ubuntu_jammy, server_type: dev1.m, location: fr-par-1, volume_size: 20 }
  aws:
    access_key_id: ${AWS_ACCESS_KEY_ID}
    secret_access_key: ${AWS_SECRET_ACCESS_KEY}
    security_group: sg-...
    subnets: [subnet-a, subnet-b]   # region derived from the AZ
    defaults: { image: ubuntu-22.04, server_type: t3.medium, location: us-east-1a, volume_size: 20, volume_type: gp3 }
```

### Tag/label abstraction

Providers use tags/labels internally to identify and track runner servers. This is an implementation detail of each provider — the shared scaling logic does not construct tag names or dicts directly.

The `CloudProvider` interface exposes:
- A generic metadata API: `get_server_tag(server, key)`, `set_server_tags(server, tags)` — provider normalises keys/values to its own format and character restrictions
- `list_servers(label_selector)` — filtered server listing
- `list_runner_servers()` — provider-managed; each provider uses its own tag names to identify servers it manages

Tag keys used by shared code must be lowercase alphanumeric and hyphens only (the most restrictive common subset across known providers). Hetzner's existing tag names (e.g. `github-hetzner-runner`) are preserved as-is inside `HetznerCloudProvider` — no migration, no breakage for existing deployments.

### Image labels for AWS

The `image-{arch}-{kind}-{name}` label format is Hetzner-specific. For AWS, jobs specify an AMI using the label format `image-ami-{id}` (e.g. `image-ami-0abc1234`). The provider is responsible for interpreting the image portion of the label in a way that makes sense for it.

### Recycling

Recycling is a provider-owned acquire/retire lifecycle. Hetzner and Scaleway
maintain stopped-server pools; dedicated static releases and re-leases fixed
hosts; AWS currently uses create/delete only. Reused servers run `recycle.sh`
by default. Hetzner can opt into image rebuild with
`providers.hetzner.recycle_with_rebuild`.

### Volumes and per-job disk

Attached cache volumes (the `volume-` label) remain Hetzner-only; AWS/Scaleway/
dedicated_static leave the interface stubs unimplemented. Separately, a
`disk-<N>` label sets the per-job **root/boot** disk minimum: resizable providers
provision it (AWS EBS root, Scaleway SBS boot), fixed-disk providers are checked
against it (Hetzner, Scaleway local-boot).

### `cloud deploy` command

`cloud deploy` is now **provider-agnostic**: it provisions the controller host
through whichever provider `config.cloud.provider` names (default `hetzner`).
Only the Hetzner path has been exercised end-to-end — AWS/Scaleway deploy is
implemented but **unvalidated**. (See follow-ups: deploy validation + secret
hygiene.)

### Testing scope

- Test the `CloudProvider` interface boundary and the two implementations against a shared test suite
- Test label parsing functions (`get_server_types`, `get_server_locations`) — pure functions, easy to test, will be modified
- One smoke test: mock provider injected into `scale_up`, verify correct provider calls on job dispatch
- Do not test individual provider SDK calls or the full scaling loop in detail

---

## Implementation Phases

### Phase 1 — Abstract interface + Hetzner refactor ✓
*No new functionality. Existing behaviour must be identical after this phase.*

- [x] Define `CloudProvider` abstract base class in `cloud_provider.py`
  - Server lifecycle: create/delete/get/list/list_runner_servers
  - Metadata: get_server_tag/set_server_tags
  - Resource discovery: get_server_type/get_location/get_image
  - SSH keys: get_or_create_ssh_key
  - Volumes: define interface methods (stubs only for now)
  - Properties: `name`, `supports_recycling`
- [x] Implement `HetznerCloudProvider` in `providers/hetzner/provider.py` by extracting Hetzner-specific code from `scale_up.py`, `scale_down.py`, and `hclient.py`
- [x] Inject provider into `scale_up` and `scale_down` (replace direct `client` usage)
- [x] Write tests for the `CloudProvider` interface against `HetznerCloudProvider` (with a mock Hetzner backend)
- [x] Verify all existing behaviour unchanged

### Phase 2 — Config changes ✓

- [x] Add `providers:` section to config schema
- [x] ~~Support backwards-compatible `hetzner_token` flat format~~ **Reversed:** flat
  `hetzner_token` and top-level `default_*` were deleted and now hard-error (see
  Config structure). No compat shims for the spin-off.
- [x] Provider factory: construct and return the right `CloudProvider` instance(s) from config

### Phase 3 — Provider type resolution ✓

- [x] When `scale_up` iterates server types, resolve which provider to use for each type name
- [x] Query each active provider: "do you have a type named X?" — use the first match
- [x] Handle the case where no provider recognises a type name (clear error message)
- [x] Update `get_server_types`, `get_server_locations`, `get_server_image` to operate on abstract provider types
- [x] Map `in-` labels to provider locations: Hetzner interprets as DC location (e.g. `nbg1`), AWS interprets as AZ (e.g. `us-east-1a`). AZ-level placement ensures future EBS volume support works without revisiting the label system.

### Phase 4 — AWS implementation

- [x] Implement `AWSCloudProvider` in `providers/aws/provider.py`
  - EC2 instance lifecycle (create/delete/get/list)
  - Tag-based server identification
  - AMI image resolution (`ami-{id}`, `ubuntu-{version}`, or `resolve:ssm:{path}`)
  - EC2 key pair management
  - No recycling (`supports_recycling = False`)
  - Volumes: `NotImplementedError` stubs
- [x] Wire AWS into `config/factory.py` (region derived from AZ)
- [x] Fix `get_server_image` to pass raw string to `provider.get_image()` (provider owns parsing)
- [x] Fix resolved loop to use `rp.default_image` per provider with `config.default_image` fallback
- [x] Run the shared provider test suite against `AWSCloudProvider` (66 tests, all passing)
- [x] Validate end-to-end with a real AWS account

### Phase 4b — More providers, config normalization, new labels ✓
*Not in the original plan; added as the refactor progressed.*

- [x] **Scaleway provider** — create/delete, SBS + local-boot modes, power-off
  recycling, cost via `get_prices()`. Validated against a real account.
- [x] **dedicated_static provider** — pre-existing hosts, reboot-scoped claims
  (`claim_ttl_minutes`), no cloud API.
- [x] **Recycling made provider-owned** — acquire/retire lifecycle behind the
  interface; the old hcloud-native path in `scale_down` is gone. Hetzner does
  power-off + optional reimage (`recycle_with_rebuild`); Scaleway power-off only;
  AWS none.
- [x] **Config normalization** — token → `providers.hetzner.token`; top-level
  `default_*` → `providers.hetzner.defaults`; per-provider `recycle` /
  `recycle_grace_period` / `max_runners` / `end_of_life` overrides; precedence /
  first-configured-seed. Old top-level keys hard-error.
- [x] **New labels** — `disk-<N>` (min root disk) and `provider-<name>` (pin).
- [x] **scale_up/scale_down require a configured provider** — dropped the silent
  "assume Hetzner from `config.hetzner_token`" fallback.
- [x] **service install normalized** — the systemd unit is built from `--config`
  for every provider; no Hetzner-specific `--hetzner-*` flag emission or special
  `HETZNER_TOKEN` env line.

### ~~Phase 5 — Config validation library~~ (scratched)

Not worth the dependency and migration churn. Much of `parse.py` is domain-specific validation (standby runner structure, meta-label shapes, script path checking, cross-field logic) that Pydantic field validators would still need to express explicitly. The file is already written and working; the realistic reduction is ~30%, not 60%.

### Phase 6 — Polish

- [ ] Update meta-label examples in config/docs to show multi-provider patterns
  (still commented out in the example configs)
- [ ] Update `servers` CLI command to list across providers (`servers.py:list`
  still builds a raw Hetzner `Client` and lists only Hetzner)
- [ ] Update dashboard to show provider per runner (not present in the dashboard
  metrics yet)
- [x] Dashboard cost is per-provider (Hetzner/Scaleway EUR, AWS USD); multi-provider
  dashboard rendering itself is **unvalidated**

### Known gaps / backlog

- [x] **Server cost metrics** — `get_prices()` added to `CloudProvider` ABC, called per provider at scale_up startup; `config.server_prices` populated from the result.
- [ ] Update `README.rst` and `docs/requirements.md` (both mention providers now; completeness unverified)
- [ ] Document `cloud deploy` provider support — **now provider-agnostic** (was "Hetzner-only"); note AWS/Scaleway unvalidated
- [x] Binary/package naming — `testflows.github.hetzner.runners` → `testflows.runners`, `github-hetzner-runners` → `tfs-runners`
- [ ] **De-Hetzner cleanup** — shared constants still Hetzner-named and the discovery
  tag is inconsistent (Hetzner `github-hetzner-runner=active`, AWS/Scaleway
  `github-runner=active`); `config.hetzner_token` remains a read-only property with
  ~15 readers that also use a raw hcloud `Client` (images/volumes/servers/estimate).
- [ ] **cloud deploy** — validate on AWS/Scaleway; secret hygiene (resolved creds
  inlined at rest in the copied config, tokens in the install argv).
- [ ] **Dashboard memory usage** — 60s default + chart keys shipped as a partial mitigation;
  per-rerun growth persists (Streamlit frontend). Follow-ups: periodic reload
  backstop (preserve tab via query params), scope `run_every` to live panels.

---

## Open Questions

- **Multi-controller isolation.** Discovery is a single global tag per provider,
  so two controllers sharing one cloud project step on each other in scale_down.
  Isolate by project, or add a per-controller identity to the discovery tag +
  recycle prefix + ssh-key label?
- **CLI-only `service install`.** The systemd unit is now built from `--config`
  only; do we still need to support installing purely from CLI flags with no
  config file (removed for Hetzner during service-install normalization)?
