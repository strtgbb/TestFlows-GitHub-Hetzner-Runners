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

## Images

The `image` default (or a job's `image-<spec>` label) is resolved in this order:

1. **Image UUID** — used directly, e.g.
   `image-33333333-3333-3333-3333-333333333333`.
2. **Marketplace label** — a public base image such as `ubuntu_jammy`
   (resolved to the zone-local image, preferring `x86_64`).
3. **Custom image name** — a private image you have created in the project, e.g.
   `image-runner-base`. Matched **by exact name** (case-insensitive) in the
   configured zone, preferring `x86_64`.

This differs from Hetzner, where a custom image is a *snapshot matched by
description* (`image-x86-snapshot-my-image`). On Scaleway a custom image is a
private Instance image; reference it by **name** or **UUID**. Custom image names
may contain `-`/`.` — unlike server types, the image value is not split on `-`.

To bake a custom image: snapshot a prepared instance's volume, create an image
from it (`scw instance image create ...`), then reference that image's name.
Phase-2 recycling will create and reference custom images this way.

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

## Recycling

Scaleway uses provider-owned no-reimage recycling. Completed Instances are
powered off and later powered on for a compatible job, then `recycle.sh` cleans
the retained filesystem before runner registration.

A powered-off Instance is not guaranteed to reacquire capacity. If activation
fails, the provider terminates that candidate and a later scale-up attempt
creates fresh capacity. Scaleway does not offer the Hetzner-only
`recycle_with_rebuild` option.
