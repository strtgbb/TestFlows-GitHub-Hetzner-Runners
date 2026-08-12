# Server and runner name prefixes
server_name_prefix = "github-runner-"
runner_name_prefix = server_name_prefix
standby_server_name_prefix = f"{server_name_prefix}standby-"
standby_runner_name_prefix = standby_server_name_prefix
recycle_server_name_prefix = f"{server_name_prefix}recycle-"

# Server SSH key label
server_ssh_key_label = "github-hetzner-runner-ssh-key"
# Server runner discovery label. The value marks a managed runner server;
# phase 3 changes it to the controller identity (see utils.derive_runner_tag).
github_runner_label = "github-hetzner-runner"
# Volume label marking a runner-owned volume (new shared constant; wired in
# phase 3, replacing the per-provider inline literals).
runner_volume_label = "github-runner-volume"
# Recycle timestamp label (stores epoch seconds when server was marked for recycling)
recycle_timestamp_label = "github-hetzner-recycle-timestamp"
recycle_image_label = "github-runner-recycle-image"

# Legacy discovery-label KEYS recognized only during migration: an un-owned
# (``=active``) server carrying one of these is adopted by the first controller
# that sees it (see the provider auto-claim in list_runner_servers). Remove once
# no pre-unification fleets remain.
legacy_runner_labels = ("github-hetzner-runner",)
