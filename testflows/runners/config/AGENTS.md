# Config AGENTS.md

- `Config.hetzner_token` serves as a backward compatibility bridge between the legacy flat config and the new nested provider configuration.
- When adding top-level parameters to `Config`, ensure the exclusion list in `Config.update()` is updated if those parameters should not be modifiable via CLI.
