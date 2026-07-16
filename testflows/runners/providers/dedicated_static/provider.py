"""Static dedicated-host implementation of the CloudProvider interface."""

from __future__ import annotations

import hashlib
import logging
import threading

from dataclasses import dataclass
from datetime import datetime, timezone

from ...cloud_provider import CloudProvider, ProviderServer, ProviderServerType
from ...constants import github_runner_label, server_ssh_key_label, runner_name_prefix
from ...errors import ServerTypeError, LocationError, ImageSpecFormatError
from ...server import ssh

logger = logging.getLogger("testflows.runners")


@dataclass
class DedicatedStaticSSHKey:
    """Local SSH key descriptor used by the static provider."""

    name: str
    public_key: str


@dataclass
class _StaticHost:
    """Static host inventory entry with in-memory lease state."""

    host_id: str
    group_name: str
    endpoint: str
    labels: set[str]
    ssh_user: str
    ssh_port: int
    ssh_key_path: str | None
    static_name: str
    # In-memory cache of the current lease, re-derived each cycle from the live
    # GitHub runner list (reconcile_runner_leases). NOT the source of truth for
    # in-flight setups — that is the durable claim marker on the host itself.
    lease_name: str | None = None


class DedicatedStaticCloudProvider(CloudProvider):
    """Dedicated static host provider.

    Hosts are long-lived and configured in YAML. The provider does not create or
    delete infrastructure via an API: create_server leases an idle host and
    delete_server releases the lease.
    """

    _RUNNER_LABEL_PREFIX = "github-dedicated-runner-label"

    # Durable per-host claim lock: a `mkdir`'d directory (atomic — one racer
    # wins, others get EEXIST), so claiming is safe across controller processes.
    # Kept under /run so host reboot clears it naturally; stale markers are also
    # reclaimed by TTL to cover abandoned setup attempts.
    _CLAIM_DIR = "/run/user/$(id -u)/testflows-github-runners"
    _CLAIM_PATH = "/run/user/$(id -u)/testflows-github-runners/claim"

    def __init__(
        self,
        groups: dict[str, dict],
        default_ssh_user: str = "root",
        claim_ttl_minutes: int = 360,
        label_prefix: str = "",
    ):
        self._default_image = None
        self._default_location = None
        # Minutes a claim marker stays authoritative before it is treated as
        # stale (a crashed/abandoned setup) and the host may be reclaimed.
        self._claim_ttl_minutes = claim_ttl_minutes
        # Routing labels carry the configured label_prefix (e.g. labels look
        # like "<prefix>type-x" / "<prefix>in-y"), so type/location extraction
        # and matching must use the prefixed form — same normalization as
        # get_server_types (tolerate a missing trailing dash).
        lp = (label_prefix or "").strip().lower()
        if lp and not lp.endswith("-"):
            lp += "-"
        self._type_label_prefix = f"{lp}type-"
        self._loc_label_prefix = f"{lp}in-"
        self._lock = threading.Lock()
        self._hosts: list[_StaticHost] = []
        self._supported_types: set[str] = set()
        self._supported_locations: set[str] = set()

        for group_name in sorted(groups):
            group = groups[group_name]
            group_labels = {label.lower() for label in group["labels"]}
            group_ssh_user = group.get("ssh_user") or default_ssh_user
            group_ssh_port = group.get("ssh_port", 22)
            group_ssh_key_path = group.get("ssh_key_path")
            for label in group_labels:
                if label.startswith(self._type_label_prefix):
                    self._supported_types.add(
                        label.split(self._type_label_prefix, 1)[1]
                    )
                elif label.startswith(self._loc_label_prefix):
                    self._supported_locations.add(
                        label.split(self._loc_label_prefix, 1)[1]
                    )

            for index, endpoint in enumerate(group["hosts"]):
                host_id = f"{group_name}:{index}"
                endpoint_id = hashlib.md5(
                    endpoint.strip().lower().encode("utf-8")
                ).hexdigest()[:12]
                static_name = (
                    f"{runner_name_prefix}static-{group_name}-{endpoint_id}"
                )
                self._hosts.append(
                    _StaticHost(
                        host_id=host_id,
                        group_name=group_name,
                        endpoint=endpoint,
                        labels=set(group_labels),
                        ssh_user=group_ssh_user,
                        ssh_port=group_ssh_port,
                        ssh_key_path=group_ssh_key_path,
                        static_name=static_name,
                    )
                )

    # ---------------------------------------------------------------------------
    # Identity
    # ---------------------------------------------------------------------------
    @property
    def name(self) -> str:
        return "dedicated_static"

    @property
    def supports_recycling(self) -> bool:
        return False

    def setup_script_name(self, labels: list[str], label_prefix: str = "") -> str:
        # Static hosts are provisioned out of band, so the setup-step is cleanup,
        # never provisioning: it defaults to recycle.sh and is selected by a
        # recycle-<name> label. A setup- label is the provisioning selector (e.g.
        # for cloud providers in a mixed fleet) and does not apply here, so it is
        # ignored.
        if label_prefix and not label_prefix.endswith("-"):
            label_prefix += "-"
        prefix = (label_prefix + "recycle-").lower()
        name = None
        for label in labels:
            label = label.lower()
            if label.startswith(prefix):
                name = label.split(prefix, 1)[1]
        return f"{name}.sh" if name is not None else "recycle.sh"

    def build_runner_name(self, server: ProviderServer) -> str:
        host = getattr(server, "_native", None)
        if host is not None and hasattr(host, "static_name"):
            return host.static_name
        raise ValueError(
            "dedicated_static runner name requires server._native.static_name"
        )

    # ---------------------------------------------------------------------------
    # Server lifecycle
    # ---------------------------------------------------------------------------
    def _as_provider_server(
        self, host: _StaticHost, include_idle: bool = False
    ) -> ProviderServer | None:
        if host.lease_name is None and not include_idle:
            return None

        labels = self.build_server_labels(sorted(host.labels))
        name = host.lease_name if host.lease_name else host.static_name
        location = next(
            (
                label.split(self._loc_label_prefix, 1)[1]
                for label in sorted(host.labels)
                if label.startswith(self._loc_label_prefix)
            ),
            "",
        )
        server_type = next(
            (
                label.split(self._type_label_prefix, 1)[1]
                for label in sorted(host.labels)
                if label.startswith(self._type_label_prefix)
            ),
            "dedicated",
        )

        return ProviderServer(
            id=host.host_id,
            name=name,
            status=CloudProvider.STATUS_RUNNING,
            public_ipv4=host.endpoint,
            private_ipv4=None,
            labels=dict(labels),
            server_type=server_type,
            location=location,
            created=datetime.now(timezone.utc),
            volumes=[],
            public_ipv6=None,
            ssh_user=host.ssh_user,
            ssh_port=host.ssh_port,
            ssh_key_path=host.ssh_key_path,
            runner_on_exit="reboot",
            _native=host,
        )

    def _set_lease(self, host: _StaticHost, runner_name: str):
        host.lease_name = runner_name

    def _clear_lease(self, host: _StaticHost):
        host.lease_name = None

    # ---------------------------------------------------------------------------
    # Durable claim marker (host-side source of truth for in-flight setups)
    # ---------------------------------------------------------------------------
    def _claim_minutes(self) -> int:
        """Claim freshness window in whole minutes (for ``find -mmin``)."""
        return max(1, int(self._claim_ttl_minutes))

    def _try_claim(self, host: _StaticHost) -> bool:
        """Atomically acquire the durable claim; True iff we now hold it.

        `mkdir` is the atomic check-and-claim — one racer wins, others get
        EEXIST — so it's safe across controller processes. A claim older than
        claim_ttl_minutes is reclaimed as stale (that reclaim has a small residual
        race; the fresh-claim path does not). ssh() returns the remote exit
        code: 0 = acquired, else held/unreachable = not acquired.
        """
        target = self._as_provider_server(host, include_idle=True)
        minutes = self._claim_minutes()
        cmd = (
            f"'mkdir -p {self._CLAIM_DIR}; "
            f"if mkdir {self._CLAIM_PATH} 2>/dev/null; then exit 0; fi; "
            f'if [ -n "$(find {self._CLAIM_PATH} -maxdepth 0 -mmin +{minutes} 2>/dev/null)" ]; then '
            f"rmdir {self._CLAIM_PATH} 2>/dev/null && mkdir {self._CLAIM_PATH} 2>/dev/null && exit 0; fi; "
            f"exit 1'"
        )
        return ssh(target, cmd, check=False, stacklevel=4) == 0

    def _clear_claim(self, host: _StaticHost) -> None:
        """Best-effort release of the claim lock."""
        target = self._as_provider_server(host, include_idle=True)
        ssh(target, f"'rm -rf {self._CLAIM_PATH}'", check=False, stacklevel=4)

    def _host_matches_request(
        self, host: _StaticHost, server_type_name: str, location_name: str | None
    ) -> bool:
        if f"{self._type_label_prefix}{server_type_name}" not in host.labels:
            return False
        if location_name and f"{self._loc_label_prefix}{location_name}" not in host.labels:
            return False
        return True

    def create_server(
        self,
        name: str,
        server_type: ProviderServerType,
        location,
        image,
        ssh_keys: list,
        labels: dict[str, str],
        volumes: list = None,
        automount: bool = False,
        public_net: object = None,
    ) -> ProviderServer | None:
        del image, ssh_keys, labels, volumes, automount, public_net
        requested_type = server_type.name
        requested_location = location.name if hasattr(location, "name") else location

        # Hosts whose durable claim marker disqualified them this call.
        skipped: set = set()
        while True:
            with self._lock:
                host = None
                for candidate in self._hosts:
                    if candidate.host_id in skipped:
                        continue
                    if not self._host_matches_request(
                        host=candidate,
                        server_type_name=requested_type,
                        location_name=requested_location,
                    ):
                        continue
                    if candidate.lease_name is not None:
                        continue
                    # Optimistic in-memory claim so concurrent create_server
                    # calls in this process cannot pick the same host.
                    host = candidate
                    self._set_lease(host, runner_name=name)
                    break

            if host is None:
                break

            # Outside the lock (SSH must not block reconcile/list): atomically
            # claim the host. Failure = held (maybe cross-process) or
            # unreachable — skip and try the next.
            if self._try_claim(host):
                return self._as_provider_server(host)

            with self._lock:
                self._clear_lease(host)
            skipped.add(host.host_id)

        # No host claimable now (all leased/mid-setup/unreachable): an expected
        # transient condition, not an error, so return None instead of raising.
        # Unknown types are rejected earlier by get_server_type.
        return None

    def release_claim(self, server: ProviderServer, *, succeeded: bool) -> None:
        """Handle post-setup claim lifecycle.

        Success keeps the durable claim in place; normal runner teardown (reboot)
        clears the boot-scoped claim path. Failure clears the claim marker and
        frees the in-memory lease so the host can be re-dispatched immediately;
        if the host is unreachable the marker clear is best-effort and TTL-based
        staleness reclaim applies.
        """
        host = getattr(server, "_native", None)
        if host is None or not isinstance(host, _StaticHost):
            return
        if succeeded:
            return
        try:
            self._clear_claim(host)
        except Exception:
            pass  # unreachable; the claim staleness timeout will reclaim it.
        with self._lock:
            self._clear_lease(host)

    def delete_server(self, server: ProviderServer) -> None:
        with self._lock:
            for host in self._hosts:
                if host.lease_name == server.name or host.host_id == server.id:
                    self._clear_lease(host)
                    return

    def get_server(self, name: str) -> ProviderServer | None:
        with self._lock:
            static_name_exists = False
            for host in self._hosts:
                if host.lease_name == name:
                    return self._as_provider_server(host)
                if host.static_name == name:
                    static_name_exists = True
        if static_name_exists:
            logger.debug(
                "Dedicated static get_server miss for %s: host exists but lease is not set",
                name,
            )
        return None

    def list_servers(self, label_selector: str = None) -> list[ProviderServer]:
        selector_key, _, selector_value = (label_selector or "").partition("=")
        with self._lock:
            servers = [self._as_provider_server(host, include_idle=True) for host in self._hosts]
        servers = [server for server in servers if server is not None]
        if selector_key and selector_value:
            return [
                server
                for server in servers
                if server.labels.get(selector_key) == selector_value
            ]
        return servers

    def power_off_server(self, server: ProviderServer) -> None:
        raise NotImplementedError("static dedicated provider cannot power off hosts")

    def power_on_server(
        self, server: ProviderServer, timeout: int | None = None
    ) -> None:
        del timeout
        raise NotImplementedError("static dedicated provider cannot power on hosts")

    def rebuild_server(self, server: ProviderServer, image_spec: object) -> None:
        raise NotImplementedError("static dedicated provider does not support rebuild")

    # ---------------------------------------------------------------------------
    # Runner identification
    # ---------------------------------------------------------------------------
    def list_runner_servers(self) -> list[ProviderServer]:
        with self._lock:
            servers = [self._as_provider_server(host) for host in self._hosts]
        return [server for server in servers if server is not None]

    def reconcile_runner_leases(self, runner_names: set[str]) -> None:
        """Reconcile the in-memory lease cache from registered GitHub runner names.

        This only adjudicates *registered* runners (the durable signal for an
        active lease). A host whose runner is live is leased under its
        ``static_name``; any other host has its in-memory lease cleared. The
        in-flight setup window — where no runner is registered yet — is covered
        by the durable claim marker checked in ``create_server``, not here.
        """
        with self._lock:
            static_runner_names = {
                name
                for name in runner_names
                if name.startswith(f"{runner_name_prefix}static-")
            }
            known_static_names = {host.static_name for host in self._hosts}
            orphan_static_runner_names = sorted(static_runner_names - known_static_names)
            matched = 0
            cleared = 0
            for host in self._hosts:
                if host.static_name in runner_names:
                    self._set_lease(host, runner_name=host.static_name)
                    matched += 1
                else:
                    self._clear_lease(host)
                    cleared += 1
            logger.debug(
                "Dedicated static reconcile summary: hosts=%d matched=%d cleared=%d static_runners_seen=%d orphan_static_names=%d",
                len(self._hosts),
                matched,
                cleared,
                len(static_runner_names),
                len(orphan_static_runner_names),
            )
            if orphan_static_runner_names:
                logger.debug(
                    "Dedicated static reconcile orphan static runner names: %s",
                    ", ".join(orphan_static_runner_names),
                )

    # ---------------------------------------------------------------------------
    # Runner label helpers
    # ---------------------------------------------------------------------------
    def get_runner_labels(self, server: ProviderServer) -> set:
        return {
            value.lower()
            for key, value in server.labels.items()
            if key.startswith(self._RUNNER_LABEL_PREFIX)
        }

    # ---------------------------------------------------------------------------
    # Tag / label operations
    # ---------------------------------------------------------------------------
    def get_server_tag(self, server: ProviderServer, key: str) -> str | None:
        return server.labels.get(key)

    def set_server_tags(self, server: ProviderServer, tags: dict[str, str]) -> None:
        server.labels = {**server.labels, **tags}

    # ---------------------------------------------------------------------------
    # SSH key management
    # ---------------------------------------------------------------------------
    def get_or_create_ssh_key(
        self, public_key: str, is_file: bool = False
    ) -> DedicatedStaticSSHKey:
        if is_file:
            with open(public_key, "r", encoding="utf-8") as fh:
                public_key_str = fh.read().strip()
        else:
            public_key_str = public_key.strip()

        key_name = hashlib.md5(public_key_str.encode("utf-8")).hexdigest()
        return DedicatedStaticSSHKey(name=key_name, public_key=public_key_str)

    # ---------------------------------------------------------------------------
    # Server metadata helpers
    # ---------------------------------------------------------------------------
    def build_server_labels(
        self, runner_labels: list[str], ssh_key_name: str = None
    ) -> dict[str, str]:
        labels = {
            f"{self._RUNNER_LABEL_PREFIX}-{i}": value for i, value in enumerate(runner_labels)
        }
        labels[github_runner_label] = "active"
        if ssh_key_name:
            labels[server_ssh_key_label] = ssh_key_name
        return labels

    def build_volume_labels(self, arch: str, os_flavor: str, os_version: str) -> dict[str, str]:
        return {}

    def validate_labels(self, labels: dict[str, str]) -> tuple[bool, str]:
        for key, value in labels.items():
            if len(key) > 128:
                return False, f"label key '{key}' exceeds 128 characters"
            if len(str(value)) > 256:
                return False, f"label value for key '{key}' exceeds 256 characters"
        return True, ""

    def update_server(
        self, server: ProviderServer, name: str, labels: dict[str, str]
    ) -> ProviderServer:
        server.name = name
        server.labels = labels
        with self._lock:
            for host in self._hosts:
                if host.host_id == server.id or host.lease_name == server.name:
                    host.lease_name = name
                    break
        return server

    # ---------------------------------------------------------------------------
    # Resource discovery
    # ---------------------------------------------------------------------------
    def get_server_type(self, name: str) -> ProviderServerType:
        type_name = name.name if hasattr(name, "name") else str(name)
        if type_name not in self._supported_types:
            raise ServerTypeError(
                f"dedicated static type '{type_name}' is not configured in providers.dedicated_static.groups"
            )
        return ProviderServerType(name=type_name, _native=type_name)

    def get_server_arch(self, server_type: ProviderServerType) -> str:
        name = server_type.name.lower()
        if "arm" in name or "aarch" in name:
            return "arm64"
        return "x64"

    def expand_location_label(self, name: str) -> list[str]:
        return [name]

    def get_location(self, name: str | None, required: bool = False) -> str | None:
        if name is None:
            if required:
                raise LocationError("location is required")
            return None
        if self._supported_locations and name not in self._supported_locations:
            raise LocationError(
                f"dedicated static location '{name}' is not configured in providers.dedicated_static.groups"
            )
        return name

    def get_image(self, image_spec: object) -> object:
        spec = str(image_spec)
        # Static dedicated hosts do not use image labels in MVP.
        raise ImageSpecFormatError(
            f"unsupported dedicated static image spec '{spec}'; image labels are not supported"
        )
