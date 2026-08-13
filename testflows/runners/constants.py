# Server and runner name prefixes
server_name_prefix = "github-runner-"
runner_name_prefix = server_name_prefix
standby_server_name_prefix = f"{server_name_prefix}standby-"
standby_runner_name_prefix = standby_server_name_prefix
recycle_server_name_prefix = f"{server_name_prefix}recycle-"

# Server SSH key label
server_ssh_key_label = "github-runner-ssh-key"
# Server runner discovery label KEY. Its value is the controller identity (see
# utils.derive_runner_tag); presence of the key marks a managed runner server.
github_runner_label = "github-runner"
# Volume labels marking a runner-owned volume + its image metadata.
runner_volume_label = "github-runner-volume"
runner_volume_arch_label = "github-runner-arch"
runner_volume_os_label = "github-runner-os"
runner_volume_os_version_label = "github-runner-os-version"
# Legacy volume-marker KEY adopted (retagged) during migration.
legacy_runner_volume_label = "github-hetzner-runner-volume"
# Recycle timestamp label (stores epoch seconds when server was marked for recycling)
recycle_timestamp_label = "github-recycle-timestamp"
recycle_image_label = "github-runner-recycle-image"

# Legacy discovery-label KEYS recognized only during migration: an un-owned
# (``=active``) server carrying one of these is adopted by the first controller
# that sees it (see the provider auto-claim in list_runner_servers). Remove once
# no pre-unification fleets remain.
legacy_runner_labels = ("github-hetzner-runner",)
