# Scaleway provider

Provisions GitHub Actions runners on [Scaleway](https://www.scaleway.com/)
Instances.

Install the optional SDK dependency:

```bash
pip install "testflows.runners[scaleway]"
```

## Instance types use the dot-form, not the dash-form

Scaleway's own instance-type names contain `-` (e.g. `DEV1-S`, `GP1-XS`,
`POP2-2C-8G`). The runner label grammar reserves `-` as a separator, so this
provider uses a **canonical dot-form** everywhere a type is named: replace every
`-` with `.` and lower-case it.

| Scaleway native | Use this (dot-form) |
| --- | --- |
| `DEV1-S` | `dev1.s` |
| `GP1-XS` | `gp1.xs` |
| `PRO2-XXS` | `pro2.xxs` |
| `POP2-2C-8G` | `pop2.2c.8g` |

This applies in two places:

- **Config / CLI default type** — `providers.scaleway.defaults.server_type: dev1.m`
  (or `--scaleway-default-server-type dev1.m`). The dash-form is rejected with a
  message pointing at the dot-form.
- **Per-job labels** — request a type with `type-dev1.s` in `runs-on`. A
  dash-form label such as `type-dev1-s` is **silently skipped** (the label
  grammar treats it as a composite label), and the job falls back to the default
  type — so always use the dot-form.

Internally the provider translates the dot-form back to Scaleway's native
dash-form only at the API boundary.

## Configuration

```yaml
config:
  providers:
    scaleway:
      access_key: ${SCW_ACCESS_KEY}
      secret_key: ${SCW_SECRET_KEY}
      project_id: ${SCW_DEFAULT_PROJECT_ID}
      organization_id: ${SCW_DEFAULT_ORGANIZATION_ID}  # optional
      ssh_user: root                                    # default
      defaults:
        image: ubuntu_jammy        # marketplace label or image UUID
        server_type: dev1.m        # dot-form
        location: fr-par-1         # zone
        volume_size: 20
```

SSH keys are registered at the project/IAM level and injected into Instances at
boot, so there is no per-server key parameter.

## Zones and location fallback

A Scaleway **zone** (`fr-par-1`, `nl-ams-2`, `pl-waw-3`, …) behaves like an
independent region: there is no VPC/subnet to configure, and the zone is the
only placement knob. To try more than one zone for a job, list **multiple
`in-` labels** — `scale_up` tries them in order and falls back to the next when
a create fails (e.g. out-of-stock):

```yaml
runs-on: [self-hosted, type-gp1.xs, in-fr-par-1, in-fr-par-2]
```

With no `in-` label, the provider's default `location` zone is used. There is no
composite location label (a zone already contains `-`, which is the label
separator) — use one `in-<zone>` label per zone. As with Hetzner, a type that
is not offered in a given zone simply fails that attempt and the loop moves on.

## Billing notes

- Scaleway CPU Instances bill **per hour** (1-hour minimum increment).
- Compute billing **pauses while an Instance is powered off**; volumes and
  reserved flexible IPs keep billing.
- A powered-off Instance **releases its physical node**, so powering one back on
  can be delayed or fail under zone/type capacity pressure. The scale-up loop
  falls back across zones (`in-` labels) and types when a create fails.

## Scope

v1 implements the **create/delete** lifecycle only
(`supports_recycling = False`), matching the AWS provider. Image-rebuild
recycling is deferred to a high-priority **phase 2**; on Scaleway the recycle
lifecycle differs from Hetzner's in-place `rebuild` (a powered-off Instance is
not guaranteed to power back on), so it needs its own design.
