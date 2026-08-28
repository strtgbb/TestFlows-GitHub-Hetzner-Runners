# Config AGENTS.md

- `Config` lives in `config_schema.py`, not here. That module is a leaf: it
  imports only stdlib, hcloud domain types, `ordered_set`, the logger default,
  and the pure `argtypes` validators. Keep it that way — providers import their
  config dataclasses from it, and any import of the `config` package or `args`
  reintroduces the cycle the layering guard test blocks.
- `Config.hetzner_token` is a read-only property derived from
  `providers.hetzner.token`. There is no flat token field — `parse.py` rejects
  a top-level `hetzner_token:` in YAML with a message pointing at
  `providers.hetzner.token` — and the property never reads the environment. It
  exists so the Hetzner-only CLI paths (`volumes.py`, `projects.py`) can reach
  the token without walking the provider tree. The `--hetzner-token` argument
  still works and creates `providers.hetzner` when the config doesn't define it;
  that's the remaining back-compat surface.
- `apply_args()` (config/config.py) copies CLI overrides using the
  `_CLI_OVERRIDABLE_FIELDS` allowlist. A new top-level `Config` field is *not*
  settable from the CLI until you add it there. Nested fields — `providers`,
  `cloud`, `standby_runners` — are skipped by the loop on purpose and applied
  by the explicit blocks below it, or by a provider's `update_from_args` hook.
