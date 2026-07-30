"""Abstract cloud provider interface for multi-cloud runner support."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import threading
from typing import Any


@dataclass
class ProviderServerType:
    """Provider-agnostic server type descriptor."""

    name: str
    # Underlying provider object (e.g. hcloud ServerType). Internal use only.
    _native: Any = field(default=None, repr=False)

    def __str__(self) -> str:
        return self.name


@dataclass
class ProviderVolume:
    """Provider-agnostic volume descriptor."""

    id: Any
    name: str
    size: int
    location: str
    labels: dict[str, str]
    status: str = ""
    # Block device path on the server (e.g. /dev/disk/by-id/...). Provider-specific.
    device_path: str = ""
    # Underlying provider object. Internal use only.
    _native: Any = field(default=None, repr=False)


@dataclass
class ProviderServer:
    """Provider-agnostic server descriptor."""

    id: Any
    name: str
    status: str
    public_ipv4: str | None
    private_ipv4: str | None
    labels: dict[str, str]
    server_type: str
    location: str
    created: datetime
    volumes: list["ProviderVolume"] = field(default_factory=list)
    public_ipv6: str | None = None
    # SSH login user for this server. Defaults to 'root' (Hetzner); override
    # for providers whose AMIs use a different default user (e.g. 'ubuntu' on AWS).
    ssh_user: str = "root"
    # SSH TCP port.
    ssh_port: int = 22
    # Optional SSH private key path to use when connecting to this host.
    ssh_key_path: str | None = None
    # Action performed when the ephemeral runner process exits.
    # Supported values: "poweroff" (default) and "reboot".
    runner_on_exit: str = "poweroff"
    # Root/boot disk size in GB, when the provider can determine it cheaply
    # (e.g. from the server type or the boot volume). None when unknown. Used to
    # decide whether a pooled server satisfies a job's minimum-disk request.
    root_disk_size: int | None = None
    # Underlying provider object (e.g. hcloud BoundServer). Internal use only.
    _native: Any = field(default=None, repr=False)


@dataclass(frozen=True)
class RecycleRequest:
    """Provider-neutral requirements for reusing an existing runner server."""

    name: str
    server_type: str
    location: str | None
    image: Any
    labels: dict[str, str]
    ssh_key_names: frozenset[str]
    candidates: tuple[ProviderServer, ...] | None = None
    volume_names: frozenset[str] = frozenset()
    enable_ipv4: bool = True
    enable_ipv6: bool = True
    timeout: int = 60
    # Minimum root/boot disk in GB the job requires (from a ``disk-`` label), or
    # None. A pooled server is only reused when its disk is known to satisfy it.
    min_disk: int | None = None


@dataclass(frozen=True)
class RecycleClaim:
    """An in-process reservation of a recyclable server."""

    server: ProviderServer
    request: RecycleRequest


@dataclass
class AcquiredServer:
    """Server returned by a provider acquisition transition."""

    server: ProviderServer
    use_recycle_script: bool


@dataclass(frozen=True)
class RetirementResult:
    """Result of retiring a runner server."""

    action: str
    server_name: str


class CloudProvider(ABC):
    """Abstract base class for cloud provider implementations.

    Each provider must implement all abstract methods. Volume operations are
    optional per provider and the base class raises NotImplementedError — providers
    that do not support volumes should leave the base implementation in place.
    """

    # Status constants — providers must map their own status strings to these.
    STATUS_RUNNING = "running"
    STATUS_OFF = "off"
    STATUS_STARTING = "initializing"
    STATUS_STOPPING = "stopping"
    STATUS_REBUILDING = "rebuilding"
    STATUS_MIGRATING = "migrating"
    STATUS_DELETING = "deleting"
    STATUS_UNKNOWN = "unknown"
    _claim_state_init_lock = threading.Lock()

    # ---------------------------------------------------------------------------
    # Identity
    # ---------------------------------------------------------------------------

    @property
    def default_image(self) -> Any:
        """Default image spec for this provider (set in __init__, None if not configured).

        Used by scale_up when no ``image-`` label is present on the job.  Each
        provider stores its own native format (e.g. a validated hcloud Image for
        Hetzner, an AMI ID string for AWS).
        """
        return getattr(self, "_default_image", None)

    @property
    def default_location(self) -> Any:
        """Default location spec for this provider (set in __init__, None if not configured).

        Used by scale_up when no ``in-`` label is present on the job.  The
        format is provider-specific (e.g. an hcloud Location for Hetzner, an
        availability-zone string for AWS).
        """
        return getattr(self, "_default_location", None)

    @property
    def ssh_user(self) -> str:
        """Login user for SSH into this provider's servers (e.g. 'root', 'ubuntu').

        Matches the ``ssh_user`` stamped onto the ``ProviderServer`` this provider
        creates. Used by cloud-deploy host mode, where there is no created server
        object to read it from. Defaults to the provider's configured ``_ssh_user``
        or 'root'.
        """
        return getattr(self, "_ssh_user", "root")

    def setup_script_name(self, labels: "list[str]", label_prefix: str = "") -> str:
        """Filename of the setup-step script run before each runner registers.

        Base: a ``setup-<name>`` label override, else ``setup.sh``. Providers
        whose setup-step is itself a cleanup script (dedicated_static) override
        this to read a ``recycle-<name>`` label instead. The caller resolves the
        path and checks existence.
        """
        if label_prefix and not label_prefix.endswith("-"):
            label_prefix += "-"
        prefix = (label_prefix + "setup-").lower()
        name = None
        for label in labels:
            label = label.lower()
            if label.startswith(prefix):
                name = label.split(prefix, 1)[1]
        return f"{name}.sh" if name is not None else "setup.sh"

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name, e.g. 'hetzner' or 'aws'."""

    @property
    def supports_volumes(self) -> bool:
        """True if this provider supports persistent volume operations.

        Defaults to False. Providers that implement create/get/list_volume
        should override this to return True.
        """
        return False

    def fixed_root_disk(self, server_type: Any) -> int | None:
        """Fixed root/boot disk size in GB for *server_type*, or None.

        Return an int when the root disk is fixed by the server type and cannot
        be resized per job (Hetzner's bundled disk; Scaleway local-boot types),
        so the scale-up loop can reject a type whose fixed disk is smaller than a
        job's ``disk-`` minimum. Return None when the root disk is resizable or
        the size is unknown (AWS, Scaleway SBS, dedicated_static) — such
        providers are never gated on the minimum and instead provision it.
        """
        return None

    @property
    def default_server_type(self) -> Any:
        """Default server-type spec for this provider (None if not configured).

        Used to seed unlabeled jobs when this provider is the first configured
        (precedence) provider. Stored in the provider's own native format (set
        in ``__init__`` from ``providers.<name>.defaults.server_type`` and
        resolved to a validated object at startup).
        """
        return getattr(self, "_default_server_type", None)

    @property
    def default_volume_size(self) -> int | None:
        """Default volume size in GB for this provider, or None if not configured."""
        return getattr(self, "_default_volume_size", None)

    @property
    def default_volume_location(self) -> Any:
        """Default volume-location spec for this provider (None if not configured)."""
        return getattr(self, "_default_volume_location", None)

    @property
    def max_runners(self) -> int | None:
        """Per-provider runner cap, or None to use the global limit."""
        return getattr(self, "_max_runners", None)

    @property
    def end_of_life(self) -> int | None:
        """Per-provider end-of-life in minutes, or None to use the global setting."""
        return getattr(self, "_end_of_life", None)

    @property
    def recycle(self) -> bool | None:
        """Per-provider recycle toggle, or None to use the global setting.

        Lets recycling be turned off for one provider (e.g. Scaleway, where the
        billing model can make pooling a cost loss) while leaving it on for
        others. Providers with no reusable pool (AWS) ignore it in practice.
        """
        return getattr(self, "_recycle", None)

    @property
    def recycle_grace_period(self) -> int | None:
        """Per-provider recycle grace period in seconds, or None for the global setting."""
        return getattr(self, "_recycle_grace_period", None)

    @property
    def currency(self) -> str:
        """ISO 4217 currency code for this provider's prices (e.g. 'EUR', 'USD')."""
        return "EUR"

    def get_prices(self) -> dict[str, dict[str, float]]:
        """Fetch current prices for this provider's server types.

        Returns a dict of {server_type: {location: hourly_price}}.
        Providers that don't support price fetching return an empty dict.
        """
        return {}

    # ---------------------------------------------------------------------------
    # Server lifecycle
    # ---------------------------------------------------------------------------

    @abstractmethod
    def create_server(
        self,
        name: str,
        server_type: Any,
        location: Any,
        image: Any,
        ssh_keys: list,
        labels: dict[str, str],
        volumes: list = None,
        public_net: Any = None,
        root_disk_size: int = None,
    ) -> "ProviderServer | None":
        """Create a new server and return a ProviderServer descriptor.

        ``root_disk_size`` (GB), when given, is the job's requested minimum root
        disk (from a ``disk-`` label). Providers with a resizable root disk
        provision at least this size; providers with a fixed root disk ignore it
        (the caller already verified the fixed disk is large enough).

        The call should block until the server object is created (though not
        necessarily until it is running). The caller is responsible for waiting
        for SSH availability.

        Return None when no server can be provisioned right now for an expected,
        transient reason (e.g. a fixed-capacity provider with all hosts in use);
        the caller cancels the attempt quietly and retries. Invalid requests
        (unknown type/location) must still raise.
        """

    @abstractmethod
    def delete_server(self, server: ProviderServer) -> None:
        """Delete the given server."""

    @abstractmethod
    def get_server(self, name: str) -> ProviderServer | None:
        """Look up a server by name. Returns None if not found."""

    @abstractmethod
    def list_servers(self, label_selector: str = None) -> list[ProviderServer]:
        """Return all servers, optionally filtered by a label selector string."""

    @abstractmethod
    def power_off_server(self, server: ProviderServer) -> None:
        """Power off (stop) the given server."""

    @abstractmethod
    def power_on_server(
        self, server: ProviderServer, timeout: int | None = None
    ) -> None:
        """Power on (start) the given server.

        Args:
            server: Provider server descriptor.
            timeout: Optional provider-specific max retries/time budget.
        """

    @abstractmethod
    def rebuild_server(self, server: ProviderServer, image_spec: Any) -> None:
        """Rebuild the server from the given image. Blocks until finished."""

    # ---------------------------------------------------------------------------
    # Runner identification
    # ---------------------------------------------------------------------------

    @abstractmethod
    def list_runner_servers(self) -> list[ProviderServer]:
        """Return all servers managed by this provider for runner usage.

        The provider is responsible for filtering by its own internal tag/label
        convention (e.g. Hetzner uses ``github-hetzner-runner=active``).
        """

    def before_scale_up(self, managed_runner_names: frozenset[str]) -> None:
        """Optional hook before scale-up provider inventory is read."""
        del managed_runner_names

    def before_scale_down(self, managed_runner_names: frozenset[str]) -> None:
        """Optional hook before scale-down provider inventory is read."""
        del managed_runner_names

    def after_scale_down(self) -> None:
        """Optional hook after each scale-down cycle."""

    def after_server_setup(
        self, server: ProviderServer, error: BaseException | None
    ) -> None:
        """Optional hook after a server setup attempt."""
        del server, error

    def claim_recycled_server(self, request: RecycleRequest) -> RecycleClaim | None:
        """Reserve a compatible recyclable server, or return None.

        Providers with a reusable stopped-server pool override this method.
        Static providers acquire reusable hosts through ``create_server``.
        """
        del request
        return None

    def recycle_image_id(self, image: Any) -> str:
        """Return a stable image identifier stored on recyclable servers."""
        image_id = getattr(image, "id", None)
        if image_id is not None:
            identity = f"id:{image_id}"
        else:
            image_name = getattr(image, "name", None)
            identity = f"name:{image_name}" if image_name else str(image)
        return hashlib.sha256(identity.encode()).hexdigest()[:32]

    def is_runner_label_tag(self, key: str) -> bool:
        """Return whether *key* stores a GitHub runner label."""
        del key
        return False

    def labels_for_recycled_server(
        self, server: ProviderServer, runner_labels: dict[str, str]
    ) -> dict[str, str]:
        """Replace stale runner labels while preserving unrelated metadata."""
        labels = {
            key: value
            for key, value in server.labels.items()
            if not self.is_runner_label_tag(key)
        }
        labels.update(runner_labels)
        return labels

    def activate_recycled_server(self, claim: RecycleClaim) -> AcquiredServer:
        """Activate a previously claimed recyclable server."""
        raise NotImplementedError(f"provider '{self.name}' has no recyclable pool")

    def release_recycle_claim(self, claim: RecycleClaim) -> None:
        """Release an in-process recyclable-server reservation."""
        lock, claimed = self._recycle_claim_state()
        with lock:
            claimed.discard(str(claim.server.id))

    def is_recycle_claimed(self, server: ProviderServer) -> bool:
        """Return whether *server* is reserved for activation."""
        lock, claimed = self._recycle_claim_state()
        with lock:
            server_id = str(server.id)
            return server_id in claimed or server_id in self._recycle_deleting_ids

    def reserve_recycled_server(self, server: ProviderServer) -> bool:
        """Atomically reserve *server* against activation or eviction."""
        lock, claimed = self._recycle_claim_state()
        with lock:
            server_id = str(server.id)
            if server_id in claimed or server_id in self._recycle_deleting_ids:
                return False
            claimed.add(server_id)
            return True

    def release_recycled_server(self, server: ProviderServer) -> None:
        """Release a reservation created for activation or eviction."""
        lock, claimed = self._recycle_claim_state()
        with lock:
            claimed.discard(str(server.id))

    def mark_recycled_server_deleting(self, server: ProviderServer) -> None:
        """Keep a tombstone reservation until deletion is observed."""
        lock, claimed = self._recycle_claim_state()
        with lock:
            server_id = str(server.id)
            claimed.discard(server_id)
            self._recycle_deleting_ids.add(server_id)

    def is_recycled_server(self, server: ProviderServer) -> bool:
        """Return whether *server* belongs to this provider's recyclable pool."""
        del server
        return False

    def retire_runner_server(
        self,
        server: ProviderServer,
        *,
        reason: str,
        recycle_enabled: bool,
        ssh_key_names: set[str],
        end_of_life: int,
        recycle_grace_period: int,
    ) -> RetirementResult:
        """Retire a runner server.

        The default lifecycle is create/delete. Reusable cloud and static
        providers override this to park or release resources.
        """
        del reason, recycle_enabled, end_of_life, recycle_grace_period
        if not self.has_matching_ssh_key(server, ssh_key_names):
            return RetirementResult("unmanaged", server.name)
        self.delete_server(server)
        return RetirementResult("deleted", server.name)

    def _recycle_claim_state(self) -> tuple[threading.Lock, set[str]]:
        """Return the provider-local lock and claimed server IDs."""
        if not hasattr(self, "_recycle_claim_lock"):
            with self._claim_state_init_lock:
                if not hasattr(self, "_recycle_claim_lock"):
                    self._recycle_claim_lock = threading.Lock()
                    self._recycle_claimed_ids = set()
                    self._recycle_deleting_ids = set()
        return self._recycle_claim_lock, self._recycle_claimed_ids

    def _claim_matching_recycled_server(
        self, request: RecycleRequest, matches
    ) -> RecycleClaim | None:
        """Reserve the first provider server accepted by *matches*."""
        servers = (
            list(request.candidates)
            if request.candidates is not None
            else self.list_runner_servers()
        )
        lock, claimed = self._recycle_claim_state()
        with lock:
            live_ids = {str(server.id) for server in servers}
            self._recycle_deleting_ids.intersection_update(live_ids)
            for server in servers:
                server_id = str(server.id)
                if server_id in claimed or server_id in self._recycle_deleting_ids:
                    continue
                if matches(server, request):
                    claimed.add(server_id)
                    return RecycleClaim(server=server, request=request)
        return None

    def build_runner_name(self, server: ProviderServer) -> str:
        """Build GitHub runner registration name for a server.

        The server name already encodes {run}-{job}-{type}; use it verbatim.
        Providers can override for a provider-specific stable identity.
        """
        return server.name

    # ---------------------------------------------------------------------------
    # Runner label helpers
    # ---------------------------------------------------------------------------

    @abstractmethod
    def get_runner_labels(self, server: ProviderServer) -> set:
        """Return the set of job labels attached to a runner server.

        Each provider stores runner labels in its own tag/key scheme.  This
        method hides that scheme and returns a plain set of lowercase label
        value strings (e.g. ``{"self-hosted", "linux", "arm64"}``).
        """

    def get_server_ssh_key_name(self, server: ProviderServer) -> str | None:
        """Return the SSH-key name stored on the server when it was created, or None.

        Used to verify a server was created by this controller (with one of its
        SSH keys) before recycling or deleting it. Each provider stores the key
        name under its own tag; the default reads the shared
        ``github-runner-ssh-key`` tag (used by AWS and Scaleway). Hetzner
        overrides this to read its ``github-hetzner-runner-ssh-key`` label.
        """
        return server.labels.get("github-runner-ssh-key")

    # ---------------------------------------------------------------------------
    # Tag / label operations
    # ---------------------------------------------------------------------------

    @abstractmethod
    def get_server_tag(self, server: ProviderServer, key: str) -> str | None:
        """Return the value of a server tag/label, or None if not present."""

    @abstractmethod
    def set_server_tags(self, server: ProviderServer, tags: dict[str, str]) -> None:
        """Update (merge) the given tags onto the server.

        Existing tags not in *tags* are preserved. The implementation should
        also update ``server.labels`` to reflect the new state.
        """

    @abstractmethod
    def has_matching_ssh_key(
        self, server: ProviderServer, ssh_key_names: set[str]
    ) -> bool:
        """Return True if *server* has an SSH key tag in *ssh_key_names*.

        Providers should return False when no key marker is present or when it
        does not match.
        """

    # ---------------------------------------------------------------------------
    # SSH key management
    # ---------------------------------------------------------------------------

    @abstractmethod
    def get_or_create_ssh_key(self, public_key: str) -> Any:
        """Ensure a key pair matching *public_key* exists in the provider.

        Returns the provider key object (e.g. hcloud SSHKey) whose name/id can
        be used when creating servers.
        """

    # ---------------------------------------------------------------------------
    # Server metadata helpers
    # ---------------------------------------------------------------------------

    @abstractmethod
    def build_server_labels(
        self, runner_labels: list[str], ssh_key_name: str = None
    ) -> dict[str, str]:
        """Return the tag/label dict to apply to a new (or recycled) runner server.

        The provider owns its own tag key naming scheme (e.g. Hetzner uses
        ``github-hetzner-runner-label-{i}``).  The returned dict should include
        both the per-label entries and the ``github_runner_label = "active"``
        marker used for server discovery.
        """

    @abstractmethod
    def build_volume_labels(
        self, arch: str, os_flavor: str, os_version: str
    ) -> dict[str, str]:
        """Return the tag/label dict to apply to a new runner volume."""

    @abstractmethod
    def validate_labels(self, labels: dict[str, str]) -> tuple[bool, str]:
        """Validate that *labels* satisfy provider-specific constraints.

        Returns ``(True, "")`` if valid, ``(False, error_message)`` otherwise.
        """

    @abstractmethod
    def update_server(
        self, server: ProviderServer, name: str, labels: dict[str, str]
    ) -> ProviderServer:
        """Rename *server* and replace its labels atomically.

        Updates ``server.name`` and ``server.labels`` in-place and returns the
        same (updated) ProviderServer for convenience.
        """

    # ---------------------------------------------------------------------------
    # Resource discovery
    # ---------------------------------------------------------------------------

    @abstractmethod
    def get_server_type(self, name: str) -> ProviderServerType:
        """Validate and return a ProviderServerType for *name*.

        Raises an appropriate error if the type does not exist.
        """

    @abstractmethod
    def get_server_arch(self, server_type: ProviderServerType) -> str:
        """Return the CPU architecture for *server_type* (``'x64'`` or ``'arm64'``)."""

    @abstractmethod
    def get_location(self, name: str, required: bool = False) -> Any:
        """Validate and return the provider location object for *name*.

        If *name* is None and *required* is False, returns None.
        Raises an appropriate error if *required* is True and name is None or
        the location does not exist.
        """

    @abstractmethod
    def get_image(self, image_spec: Any) -> Any:
        """Validate and return the provider image object for *image_spec*.

        The format of *image_spec* is provider-specific. For Hetzner this is an
        ``hcloud.images.domain.Image`` descriptor; for AWS it may be an AMI id
        string.

        Raises an appropriate error if the image does not exist.
        """

    def expand_location_label(self, name: str) -> list[str]:
        """Expand a (possibly composite) location label into individual location names.

        The default implementation treats every label as a single location and
        returns ``[name]``.  Providers that support composite location labels
        (e.g. Hetzner's ``hel1-fsn1-nbg1`` shorthand for "any of these DCs")
        should override this method to split the composite into its component
        parts so that scale_up can try each location in preference order.

        Args:
            name: Raw location string extracted from an ``in-<name>`` job label.

        Returns:
            List of individual location name strings.  For simple labels this
            is always a one-element list.
        """
        return [name]

    # ---------------------------------------------------------------------------
    # Volume operations (optional — providers that don't support volumes leave
    # the base NotImplementedError in place)
    # ---------------------------------------------------------------------------

    def create_volume(
        self,
        name: str,
        size: int,
        location: Any,
        labels: dict[str, str] = None,
        format: str = "ext4",
        automount: bool = False,
    ) -> ProviderVolume:
        """Create a new volume. Optional per provider."""
        raise NotImplementedError(
            f"Provider '{self.name}' does not support volume creation"
        )

    def delete_volume(self, volume: ProviderVolume) -> None:
        """Delete a volume. Optional per provider."""
        raise NotImplementedError(
            f"Provider '{self.name}' does not support volume deletion"
        )

    def get_volume(self, name: str) -> ProviderVolume | None:
        """Look up a volume by name. Optional per provider."""
        raise NotImplementedError(
            f"Provider '{self.name}' does not support volume lookup"
        )

    def list_volumes(self, label_selector: str = None) -> list[ProviderVolume]:
        """Return volumes, optionally filtered by label selector. Optional per provider."""
        raise NotImplementedError(
            f"Provider '{self.name}' does not support listing volumes"
        )

    def resize_volume(self, volume: ProviderVolume, size: int) -> None:
        """Resize a volume to *size* GB. Optional per provider."""
        raise NotImplementedError(
            f"Provider '{self.name}' does not support volume resize"
        )
