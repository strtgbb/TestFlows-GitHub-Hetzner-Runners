"""Hetzner Cloud implementation of the CloudProvider interface."""

import base64
import hashlib

from hcloud.ssh_keys.domain import SSHKey
from hcloud.images.domain import Image
from hcloud.server_types.domain import ServerType
from hcloud.locations.domain import Location

from ...hclient import HClient
from ...actions import Action
from ...cloud_provider import (
    AcquiredServer,
    CloudProvider,
    ProviderServer,
    ProviderServerType,
    ProviderVolume,
    RecycleClaim,
    RecycleRequest,
    RetirementResult,
)
from ...recycling import (
    activate_recycled_server,
    recyclable_server_matches,
    retire_to_recycle_pool,
)
from ...errors import ImageSpecFormatError
from . import config as hetzner_config
from ...constants import (
    github_runner_label,
    recycle_server_name_prefix,
    recycle_timestamp_label,
    server_ssh_key_label,
    legacy_runner_labels,
    runner_label_key_prefix,
    runner_volume_label,
    runner_volume_arch_label,
    runner_volume_os_label,
    runner_volume_os_version_label,
    legacy_runner_volume_label,
)
from ...utils import derive_runner_tag

# Legacy Hetzner label keys -> neutral names, renamed in place on claim:
# server labels here, volume-metadata labels below.
_HETZNER_LEGACY_RENAMES = {
    "github-hetzner-runner-ssh-key": server_ssh_key_label,
    "github-hetzner-recycle-timestamp": recycle_timestamp_label,
}
_HETZNER_VOLUME_RENAMES = {
    "github-hetzner-runner-arch": runner_volume_arch_label,
    "github-hetzner-runner-os": runner_volume_os_label,
    "github-hetzner-runner-os-version": runner_volume_os_version_label,
}
from .utils import _HETZNER_DC_CODE_RE, _STATUS_MAP, _server_to_provider, _volume_to_provider


class HetznerCloudProvider(CloudProvider):
    """Hetzner Cloud implementation of CloudProvider.

    Wraps HClient (hcloud.Client subclass) and delegates to the existing
    validation helpers in providers/hetzner/config.py.
    """

    config_key = "hetzner"
    precedence = 0

    @classmethod
    def from_config(cls, config) -> "HetznerCloudProvider | None":
        """Construct from Config, or None if Hetzner is not configured."""
        cfg = config.providers.hetzner
        if not (cfg and cfg.token):
            return None
        defaults = cfg.defaults
        provider = cls(
            token=cfg.token,
            default_image=defaults.image,
            default_server_type=defaults.server_type,
            default_location=defaults.location,
            default_volume_size=defaults.volume_size,
            default_volume_location=defaults.volume_location,
            max_runners=cfg.max_runners,
            end_of_life=cfg.end_of_life,
            recycle=cfg.recycle,
            recycle_grace_period=cfg.recycle_grace_period,
            recycle_with_rebuild=cfg.recycle_with_rebuild,
        )
        provider._runner_tag = derive_runner_tag(
            config.github_repository, config.with_label
        )
        return provider

    def __init__(
        self,
        token: str,
        default_image=None,
        default_server_type=None,
        default_location=None,
        default_volume_size=None,
        default_volume_location=None,
        max_runners: int = None,
        end_of_life: int = None,
        recycle: bool = None,
        recycle_grace_period: int = None,
        recycle_with_rebuild: bool = False,
    ):
        """Initialise the provider.

        Args:
            token: Hetzner Cloud API token.
            default_image: Default image spec used when no ``image-`` label is
                present (from ``providers.hetzner.defaults.image``). Resolved to
                a validated hcloud ``Image`` at startup.
            default_server_type: Default server-type spec used when no ``type-``
                label is present. Resolved to a validated type at startup.
            default_location: Default location spec used when no ``in-`` label is
                present. Resolved to a validated ``Location`` at startup.
            default_volume_size: Default volume size in GB.
            default_volume_location: Default volume-location spec. Resolved at
                startup.
            max_runners: Per-provider runner cap (overrides global max_runners).
            end_of_life: Per-provider end-of-life in minutes (overrides global).
        """
        self._client = HClient(token=token)
        self._default_image = default_image
        self._default_server_type = default_server_type
        self._default_location = default_location
        self._default_volume_size = default_volume_size
        self._default_volume_location = default_volume_location
        self._max_runners = max_runners
        self._end_of_life = end_of_life
        self._recycle = recycle
        self._recycle_grace_period = recycle_grace_period
        self._recycle_with_rebuild = recycle_with_rebuild
        self._runner_tag = "active"

    # ---------------------------------------------------------------------------
    # Identity
    # ---------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "hetzner"

    @property
    def supports_volumes(self) -> bool:
        return True

    def fixed_root_disk(self, server_type) -> int | None:
        """Hetzner's root disk is fixed by the server type (hcloud ``disk``, GB)."""
        native = getattr(server_type, "_native", None)
        disk = getattr(native, "disk", None)
        return int(disk) if disk else None

    # ---------------------------------------------------------------------------
    # Server lifecycle
    # ---------------------------------------------------------------------------

    def create_server(
        self,
        name: str,
        server_type: ProviderServerType,
        location,
        image,
        ssh_keys: list,
        labels: dict[str, str],
        volumes: list = None,
        public_net=None,
        root_disk_size: int = None,
    ) -> ProviderServer:
        """Create a server and return a ProviderServer wrapping the BoundServer.

        ``root_disk_size`` is ignored: Hetzner's root disk is fixed by the server
        type. The scale-up loop already verified (via ``fixed_root_disk``) that
        the type's disk meets any requested minimum before reaching here.
        """
        del root_disk_size
        # De-dupe by key id; Hetzner rejects duplicate ssh_keys.
        _seen = set()
        _unique_keys = []
        for _key in ssh_keys:
            if _key.id not in _seen:
                _seen.add(_key.id)
                _unique_keys.append(_key)
        ssh_keys = _unique_keys
        response = self._client.servers.create(
            name=name,
            # Unwrap ProviderServerType; accept raw ServerType for callers that
            # haven't migrated yet (e.g. recycle_server in scale_up.py).
            server_type=server_type._native if isinstance(server_type, ProviderServerType) else server_type,
            location=location,
            image=image,
            ssh_keys=ssh_keys,
            labels=labels,
            volumes=volumes or [],
            # Volumes are mounted by the setup/startup scripts, not at boot.
            automount=False,
            public_net=public_net,
        )
        bound_server: BoundServer = response.server
        # Propagate volume attachment bookkeeping the caller expects.
        if volumes:
            bound_server.volumes = volumes
            for vol in volumes:
                vol.server = bound_server
        return _server_to_provider(bound_server)

    def delete_server(self, server: ProviderServer) -> None:
        """Delete the server via its native BoundServer."""
        native: BoundServer = server._native
        native.delete()

    def get_server(self, name: str) -> ProviderServer | None:
        """Look up a server by name."""
        bound = self._client.servers.get_by_name(name=name)
        if bound is None:
            return None
        return _server_to_provider(bound)

    def list_servers(self, label_selector: str = None) -> list[ProviderServer]:
        """List servers, optionally filtered by label selector."""
        servers = self._client.servers.get_all(label_selector=label_selector)
        return [_server_to_provider(s) for s in servers]

    def power_off_server(self, server: ProviderServer) -> None:
        """Power off the server."""
        native: BoundServer = server._native
        native.power_off()

    def power_on_server(
        self, server: ProviderServer, timeout: int | None = None
    ) -> None:
        """Power on the server and wait for the action to finish."""
        native: BoundServer = server._native
        native.power_on().wait_until_finished(max_retries=timeout or 300)

    def rebuild_server(self, server: ProviderServer, image_spec) -> None:
        """Rebuild the server with the given image. Blocks until finished."""
        from hcloud import APIException

        native: BoundServer = server._native
        try:
            native.rebuild(image=image_spec).action.wait_until_finished(
                max_retries=300
            )
        except APIException as exc:
            raise APIException(
                code=exc.code,
                message=f"error while rebuilding server {native.name}: {exc.message}",
                details=getattr(exc, "details", None),
            ) from exc

    # ---------------------------------------------------------------------------
    # Runner identification
    # ---------------------------------------------------------------------------

    def _legacy_runner_selectors(self) -> list[str]:
        return super()._legacy_runner_selectors() + [
            f"{key}=active" for key in legacy_runner_labels
        ]

    def _claim_server(self, server: ProviderServer) -> None:
        """Adopt a legacy Hetzner server in place, renaming every legacy key to
        the neutral scheme (server keeps running; no delete-recreate)."""
        labels = server.labels or {}
        changes = {github_runner_label: self._runner_tag}
        for old, new in _HETZNER_LEGACY_RENAMES.items():
            if old in labels:
                changes[new] = labels[old]
                changes[old] = None
        for key in labels:
            if key in legacy_runner_labels:
                changes[key] = None
            elif key.startswith("github-hetzner-runner-label"):
                changes[key.replace("github-hetzner-runner-", "github-runner-", 1)] = (
                    labels[key]
                )
                changes[key] = None
        self.set_server_tags(server, changes)

    def is_recycled_server(self, server: ProviderServer) -> bool:
        return server.name.startswith(recycle_server_name_prefix)

    def claim_recycled_server(self, request: RecycleRequest) -> RecycleClaim | None:
        return self._claim_matching_recycled_server(
            request,
            lambda server, req: recyclable_server_matches(
                self, server, req,
                # A rebuild reimages the server, so its old image need not match.
                require_image_match=not self._recycle_with_rebuild,
            ),
        )

    def is_runner_label_tag(self, key: str) -> bool:
        return key.startswith(runner_label_key_prefix)

    def activate_recycled_server(
        self, claim: RecycleClaim
    ) -> AcquiredServer | None:
        return activate_recycled_server(
            self,
            claim,
            rebuild=self._recycle_with_rebuild,
        )

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
        del reason
        return retire_to_recycle_pool(
            self,
            server,
            recycle_enabled=recycle_enabled,
            ssh_key_names=ssh_key_names,
            end_of_life=end_of_life,
            recycle_grace_period=recycle_grace_period,
        )

    # ---------------------------------------------------------------------------
    # Runner label helpers
    # ---------------------------------------------------------------------------

    def get_runner_labels(self, server: ProviderServer) -> set:
        """Return the job labels attached to this runner server.

        Hetzner stores each label value under a numbered key with the prefix
        ``github-runner-label``.  This extracts just the values.
        """
        return {
            value.lower()
            for key, value in server.labels.items()
            if key.startswith(runner_label_key_prefix)
        }

    # ---------------------------------------------------------------------------
    # Server metadata helpers
    # ---------------------------------------------------------------------------

    def build_server_labels(
        self, runner_labels: list[str], ssh_key_name: str = None
    ) -> dict[str, str]:
        """Return Hetzner tag dict for a runner server.

        Each runner label is stored under a numbered ``github-runner-label-{i}``
        key so it satisfies Hetzner's label value constraints.
        """
        labels = {
            f"{runner_label_key_prefix}-{i}": value
            for i, value in enumerate(runner_labels)
        }
        if ssh_key_name:
            labels[server_ssh_key_label] = ssh_key_name
        labels[github_runner_label] = self._runner_tag
        return labels

    def validate_labels(self, labels: dict[str, str]) -> tuple[bool, str]:
        """Validate labels against Hetzner's label constraints."""
        from hcloud.helpers.labels import LabelValidator

        return LabelValidator.validate_verbose(labels=labels)

    def update_server(
        self, server: ProviderServer, name: str, labels: dict[str, str]
    ) -> ProviderServer:
        """Rename the server and replace its labels atomically."""
        native: BoundServer = server._native
        native.update(name=name, labels=labels)
        # Keep ProviderServer in sync with the updated native state.
        server.name = name
        server.labels = labels
        return server

    # ---------------------------------------------------------------------------
    # Tag / label operations
    # ---------------------------------------------------------------------------

    def get_server_ssh_key_name(self, server: ProviderServer) -> str | None:
        """Return the SSH-key name stored under Hetzner's ssh-key label, or None."""
        return server.labels.get(server_ssh_key_label)

    def get_server_tag(self, server: ProviderServer, key: str) -> str | None:
        """Return the value of a server label, or None."""
        return server.labels.get(key)

    def set_server_tags(self, server: ProviderServer, tags: dict[str, str]) -> None:
        """Merge *tags* onto the server and update the ProviderServer labels.

        A tag with value ``None`` removes that label."""
        native: BoundServer = server._native
        updated_labels = dict(native.labels or {})
        for k, v in tags.items():
            if v is None:
                updated_labels.pop(k, None)
            else:
                updated_labels[k] = v
        native.update(labels=updated_labels)
        server.labels = updated_labels

    def has_matching_ssh_key(
        self, server: ProviderServer, ssh_key_names: set[str]
    ) -> bool:
        """Return True when the server SSH key tag matches a known key name."""
        key_name = server.labels.get(server_ssh_key_label)
        return key_name in ssh_key_names if key_name is not None else False

    # ---------------------------------------------------------------------------
    # SSH key management
    # ---------------------------------------------------------------------------

    def get_or_create_ssh_key(self, public_key: str, is_file: bool = False) -> SSHKey:
        """Ensure an SSH key matching *public_key* exists in Hetzner Cloud.

        Args:
            public_key: The public key string (or path when is_file=True).
            is_file: If True, treat *public_key* as a file path and read it.

        Returns:
            The hcloud SSHKey object (BoundSSHKey).
        """

        def fingerprint(key_str: str) -> str:
            encoded_key = base64.b64decode(key_str.strip().split()[1].encode("utf-8"))
            md5_digest = hashlib.md5(encoded_key).hexdigest()
            return ":".join(a + b for a, b in zip(md5_digest[::2], md5_digest[1::2]))

        if is_file:
            with open(public_key, "r", encoding="utf-8") as fh:
                public_key_str = fh.read()
        else:
            public_key_str = public_key

        public_key_str = public_key_str.strip()

        key_name = hashlib.md5(public_key_str.encode("utf-8")).hexdigest()
        key_fp = fingerprint(public_key_str)

        existing = self._client.ssh_keys.get_by_fingerprint(fingerprint=key_fp)
        if not existing:
            with Action(
                f"Creating SSH key {key_name} with fingerprint {key_fp}",
                stacklevel=3,
            ):
                ssh_key = self._client.ssh_keys.create(
                    name=key_name, public_key=public_key_str
                )
        else:
            ssh_key = existing

        return ssh_key

    # ---------------------------------------------------------------------------
    # Resource discovery
    # ---------------------------------------------------------------------------

    def get_server_type(self, name) -> ProviderServerType:
        """Validate and return a ProviderServerType for *name*.

        Accepts either a plain string name or a ``ServerType`` object (the
        latter from recycle_server, already validated). Delegates to the
        existing ``check_server_type`` helper.
        """
        native_name = name.name if isinstance(name, ServerType) else name
        native = hetzner_config.check_server_type(self._client, ServerType(name=native_name))
        return ProviderServerType(name=native.name, _native=native)

    def get_server_arch(self, server_type: ProviderServerType) -> str:
        """Return the CPU architecture for *server_type*.

        Hetzner ARM64 types use the ``cax`` prefix (CAX11, CAX21, CAX31, CAX41).
        """
        if server_type.name.lower().startswith("ca"):
            return "arm64"
        return "x64"

    def get_location(self, name, required: bool = False) -> Location | None:
        """Validate and return the hcloud Location for *name*.

        Accepts either a ``Location`` object, a plain string, or None.
        Delegates to ``check_location``.
        """
        if isinstance(name, Location) or name is None:
            return hetzner_config.check_location(self._client, name, required=required)
        return hetzner_config.check_location(
            self._client, Location(name=name), required=required
        )

    def get_image(self, image_spec) -> Image:
        """Validate and return the hcloud Image for *image_spec*.

        Accepts either:
        - An ``hcloud.images.domain.Image`` descriptor (existing validated object)
        - A raw spec string in ``arch:type:name`` (colon-separated) or
          ``arch-type-name`` (hyphen-separated) format, e.g.
          ``"x86:system:ubuntu-22.04"`` or ``"x86-system-ubuntu-22.04"``.

        Raises ImageSpecFormatError if the string is not in a recognised Hetzner
        format (e.g. an AWS AMI ID), so get_server_image can skip it when
        resolving images for a multi-cloud job.
        """
        if isinstance(image_spec, str):
            from argparse import ArgumentTypeError
            from ...argtypes import image_type as _parse_hetzner_image

            sep = ":" if ":" in image_spec else "-"
            try:
                image_spec = _parse_hetzner_image(image_spec, separator=sep)
            except ArgumentTypeError as e:
                raise ImageSpecFormatError(str(e)) from e
        return hetzner_config.check_image(self._client, image_spec)

    def expand_location_label(self, name: str) -> list[str]:
        """Expand a composite Hetzner DC label into individual DC names.

        A composite label like ``hel1-fsn1-nbg1`` means "any of these
        datacentres" and is split into ``['hel1', 'fsn1', 'nbg1']`` so that
        scale_up tries each location in order.  A simple label like ``nbg1``
        is returned as ``['nbg1']``.
        """
        parts = name.split("-")
        if len(parts) > 1 and all(_HETZNER_DC_CODE_RE.match(p) for p in parts):
            return parts
        return [name]

    # ---------------------------------------------------------------------------
    # Volume operations
    # ---------------------------------------------------------------------------

    def create_volume(
        self,
        name: str,
        size: int,
        location,
        labels: dict[str, str] = None,
        format: str = "ext4",
        automount: bool = False,
    ) -> ProviderVolume:
        """Create a Hetzner volume."""
        response = self._client.volumes.create(
            name=name,
            size=size,
            location=location,
            labels=labels or {},
            format=format,
            automount=automount,
        )
        new_vol = response.volume
        response.action.wait_until_finished()
        new_vol.reload()
        return _volume_to_provider(new_vol)

    def delete_volume(self, volume: ProviderVolume) -> None:
        """Delete a Hetzner volume."""
        native: BoundVolume = volume._native
        native.delete()

    def get_volume(self, name: str) -> ProviderVolume | None:
        """Look up a volume by name."""
        bound = self._client.volumes.get_by_name(name=name)
        if bound is None:
            return None
        return _volume_to_provider(bound)

    def list_volumes(self, label_selector: str = None) -> list[ProviderVolume]:
        """List Hetzner volumes, optionally filtered by label selector.

        Overrides the base-class stub so Hetzner volumes can be retrieved
        without accessing ``_client`` directly from generic paths.
        """
        vols = self._client.volumes.get_all(label_selector=label_selector)
        return [_volume_to_provider(v) for v in vols]

    def list_runner_volumes(self) -> list[ProviderVolume]:
        """Active runner caching volumes, adopting legacy-tagged ones in place.

        Persistent volumes don't roll over, so legacy-labelled caching volumes
        are retagged to the neutral scheme when discovered (no orphaned cache).
        """
        by_id = {
            v.id: v
            for v in self._client.volumes.get_all(
                label_selector=f"{runner_volume_label}=active"
            )
        }
        for v in self._client.volumes.get_all(
            label_selector=f"{legacy_runner_volume_label}=active"
        ):
            if v.id not in by_id:
                self._claim_volume(v)
                by_id[v.id] = v
        return [_volume_to_provider(v) for v in by_id.values()]

    def _claim_volume(self, volume: "BoundVolume") -> None:
        """Retag a legacy caching volume to the neutral scheme in place."""
        labels = dict(volume.labels or {})
        new_labels = {
            k: value
            for k, value in labels.items()
            if k != legacy_runner_volume_label and k not in _HETZNER_VOLUME_RENAMES
        }
        new_labels[runner_volume_label] = labels.get(legacy_runner_volume_label, "active")
        for old, new in _HETZNER_VOLUME_RENAMES.items():
            if old in labels:
                new_labels[new] = labels[old]
        volume.update(labels=new_labels)
        volume.labels = new_labels

    def resize_volume(self, volume: ProviderVolume, size: int) -> None:
        """Resize a Hetzner volume."""
        native: BoundVolume = volume._native
        native.resize(size).wait_until_finished()
        native.size = size
        volume.size = size

    # ---------------------------------------------------------------------------
    # Prices (Hetzner-specific convenience — not part of the abstract interface)
    # ---------------------------------------------------------------------------

    def get_prices(self) -> dict[str, dict[str, float]]:
        """Return a mapping of server_type_name -> location_name -> hourly_gross_price."""
        return hetzner_config.check_prices(self._client)
