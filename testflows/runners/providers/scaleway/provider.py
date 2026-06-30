"""Scaleway implementation of the CloudProvider interface.

Uses the official ``scaleway`` SDK (optional dependency:
``pip install testflows.runners[scaleway]``).

v1 scope: create/delete lifecycle only (``supports_recycling = False``), matching
the AWS provider.  Image-rebuild recycling is deferred to a high-priority phase 2
because, against the current long-job workload, Scaleway's hourly billing makes
recycling a marginal cost win, and Scaleway's recycle lifecycle differs from
hcloud's in-place ``rebuild`` (a powered-off Instance releases its node and may
fail to power back on under capacity pressure).

Type translation: every type that crosses the SDK boundary is converted between
the canonical dot-form used by the orchestrator (``dev1.s``) and Scaleway's
native dash-form (``DEV1-S``) via :func:`utils.native_type` / :func:`utils.canonical_type`.
"""

import time
import hashlib

from ...actions import Action
from ...cloud_provider import CloudProvider, ProviderServer, ProviderServerType
from ...errors import ServerTypeError, ImageError, ImageSpecFormatError, LocationError
from .utils import (
    _RUNNER_TAG,
    _RUNNER_LABEL_TAG_PREFIX,
    _SSH_KEY_TAG,
    _ACTIVE_STATES,
    _ARM64_RE,
    canonical_type,
    native_type,
    tags_to_dict,
    dict_to_tags,
    _server_to_provider,
)
from .args import _ZONE_RE


class ScalewaySSHKey:
    """Minimal SSH-key descriptor returned by ``get_or_create_ssh_key``."""

    def __init__(self, name: str, id: str = None):
        self.name = name
        self.id = id


class ScalewayCloudProvider(CloudProvider):
    """Scaleway Instances implementation of CloudProvider.

    Recycling is not supported in v1 (``supports_recycling = False``).
    Volume operations raise ``NotImplementedError`` (inherited from base class).
    """

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        project_id: str,
        organization_id: str = None,
        zone: str = "fr-par-1",
        default_image_spec: str = None,
        default_location_spec: str = None,
        ssh_user: str = "root",
        max_runners: int = None,
        end_of_life: int = None,
    ):
        from scaleway import Client
        from scaleway.instance.v1 import InstanceV1API

        self._client = Client(
            access_key=access_key,
            secret_key=secret_key,
            default_project_id=project_id,
            default_organization_id=organization_id,
            default_zone=zone,
        )
        self._instance = InstanceV1API(self._client)
        self._project_id = project_id
        self._organization_id = organization_id
        self._zone = zone
        self._default_image = default_image_spec
        self._default_location = default_location_spec
        self._ssh_user = ssh_user
        self._max_runners = max_runners
        self._end_of_life = end_of_life

    # ---------------------------------------------------------------------------
    # Identity
    # ---------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "scaleway"

    @property
    def currency(self) -> str:
        return "EUR"

    @property
    def supports_recycling(self) -> bool:
        # Deferred to phase 2 (see module docstring).
        return False

    def get_prices(self) -> dict[str, dict[str, float]]:
        from .estimate import check_prices

        return check_prices(self._client, zones=[self._zone])

    # ---------------------------------------------------------------------------
    # Server lifecycle
    # ---------------------------------------------------------------------------

    def _wait_for_state(
        self, server_id: str, zone: str, states: set, timeout: int = 300
    ):
        """Poll ``get_server`` until the server reaches one of *states*.

        The SDK exposes no wait helper for our state set, so we poll. Returns
        the SDK ``Server`` object on success; raises TimeoutError otherwise.
        """
        deadline = time.time() + timeout
        server = None
        while time.time() < deadline:
            server = self._instance.get_server(server_id=server_id, zone=zone).server
            if (server.state or "").lower() in states:
                return server
            time.sleep(3)
        raise TimeoutError(
            f"server {server_id} did not reach state {states} within {timeout}s "
            f"(last state: {getattr(server, 'state', 'unknown')})"
        )

    def create_server(
        self,
        name: str,
        server_type: ProviderServerType,
        location,
        image,
        ssh_keys: list,
        labels: dict,
        volumes: list = None,
        automount: bool = False,
        public_net=None,
    ) -> ProviderServer:
        """Create a Scaleway Instance, power it on, and return a ProviderServer.

        ``ssh_keys`` is accepted for interface compatibility but not passed to
        the API: Scaleway injects the project's registered SSH keys at boot
        (see ``get_or_create_ssh_key``).  ``volumes``/``automount``/``public_net``
        are ignored in v1.
        """
        from scaleway.instance.v1 import ServerAction

        zone = location or self._zone
        commercial_type = native_type(server_type.name)

        # Scaleway create_server provisions a *stopped* server; we power it on
        # afterwards.  dynamic_ip_required attaches an automatic public IPv4.
        # The SDK exposes this as the single-underscore ``_create_server``.
        created = self._instance._create_server(
            zone=zone,
            name=name,
            commercial_type=commercial_type,
            image=image,
            dynamic_ip_required=True,
            protected=False,
            tags=dict_to_tags(labels),
            project=self._project_id,
        )
        server = created.server

        self._instance.server_action(
            server_id=server.id, zone=zone, action=ServerAction.POWERON
        )
        with Action(f"Waiting for Scaleway instance {name} to start", stacklevel=3):
            server = self._wait_for_state(
                server.id, zone, states={"running"}, timeout=300
            )

        return _server_to_provider(server, ssh_user=self._ssh_user)

    def delete_server(self, server: ProviderServer) -> None:
        """Terminate the Instance, freeing its compute, local volume, and IP.

        ``terminate`` is used (rather than ``delete``) so the attached volume
        and dynamic IP are released too — otherwise they keep billing.
        """
        from scaleway.instance.v1 import ServerAction

        self._instance.server_action(
            server_id=server.id, zone=server.location, action=ServerAction.TERMINATE
        )

    def get_server(self, name: str) -> ProviderServer | None:
        servers = self._instance.list_servers_all(zone=self._zone, name=name)
        for server in servers or []:
            if server.name == name and (server.state or "").lower() in _ACTIVE_STATES:
                return _server_to_provider(server, ssh_user=self._ssh_user)
        return None

    def list_servers(self, label_selector: str = None) -> list[ProviderServer]:
        """List Instances, optionally filtered by a ``key=value`` tag selector."""
        tags = [label_selector] if label_selector and "=" in label_selector else None
        servers = self._instance.list_servers_all(zone=self._zone, tags=tags)
        return [
            _server_to_provider(s, ssh_user=self._ssh_user)
            for s in (servers or [])
            if (s.state or "").lower() in _ACTIVE_STATES
        ]

    def power_off_server(self, server: ProviderServer) -> None:
        from scaleway.instance.v1 import ServerAction

        self._instance.server_action(
            server_id=server.id, zone=server.location, action=ServerAction.POWEROFF
        )

    def power_on_server(
        self, server: ProviderServer, timeout: int | None = None
    ) -> None:
        from scaleway.instance.v1 import ServerAction

        self._instance.server_action(
            server_id=server.id, zone=server.location, action=ServerAction.POWERON
        )
        self._wait_for_state(
            server.id, server.location, states={"running"}, timeout=timeout or 300
        )

    def rebuild_server(self, server: ProviderServer, image_spec) -> None:
        raise NotImplementedError(
            "Scaleway provider does not support server recycling in v1 "
            "(image-rebuild recycling is planned for phase 2)"
        )

    # ---------------------------------------------------------------------------
    # Runner identification
    # ---------------------------------------------------------------------------

    def list_runner_servers(self) -> list[ProviderServer]:
        return self.list_servers(label_selector=f"{_RUNNER_TAG}=active")

    # ---------------------------------------------------------------------------
    # Runner label helpers
    # ---------------------------------------------------------------------------

    def get_runner_labels(self, server: ProviderServer) -> set:
        return {
            value.lower()
            for key, value in server.labels.items()
            if key.startswith(_RUNNER_LABEL_TAG_PREFIX)
        }

    # ---------------------------------------------------------------------------
    # Server metadata helpers
    # ---------------------------------------------------------------------------

    def build_server_labels(
        self, runner_labels: list, ssh_key_name: str = None
    ) -> dict:
        """Return the tag dict for a runner Instance."""
        labels = {
            f"{_RUNNER_LABEL_TAG_PREFIX}-{i}": value
            for i, value in enumerate(runner_labels)
        }
        if ssh_key_name:
            labels[_SSH_KEY_TAG] = ssh_key_name
        labels[_RUNNER_TAG] = "active"
        return labels

    def build_volume_labels(self, arch: str, os_flavor: str, os_version: str) -> dict:
        """Return the tag dict for a runner volume (for future volume support)."""
        return {
            "github-runner-volume": "active",
            "github-runner-arch": arch,
            "github-runner-os": os_flavor,
            "github-runner-os-version": os_version,
        }

    def validate_labels(self, labels: dict) -> tuple[bool, str]:
        """Validate Scaleway tag constraints.

        Scaleway tags are a flat list of strings; we encode labels as
        ``key=value`` strings.  Each tag string must be <= 255 chars and a tag
        key must not contain ``=`` (it is the reserved separator).
        """
        for key, value in labels.items():
            if "=" in key:
                return False, f"tag key '{key}' must not contain '='"
            tag = f"{key}={value}" if value != "" else key
            if len(tag) > 255:
                return False, f"tag '{tag}' exceeds 255 characters"
        return True, ""

    def update_server(
        self, server: ProviderServer, name: str, labels: dict
    ) -> ProviderServer:
        """Rename the Instance and replace its tags."""
        self._instance._update_server(
            server_id=server.id,
            zone=server.location,
            name=name,
            tags=dict_to_tags(labels),
        )
        server.name = name
        server.labels = labels
        return server

    # ---------------------------------------------------------------------------
    # Tag / label operations
    # ---------------------------------------------------------------------------

    def get_server_tag(self, server: ProviderServer, key: str) -> str | None:
        return server.labels.get(key)

    def set_server_tags(self, server: ProviderServer, tags: dict) -> None:
        merged = {**(server.labels or {}), **tags}
        self._instance._update_server(
            server_id=server.id, zone=server.location, tags=dict_to_tags(merged)
        )
        server.labels = merged

    # ---------------------------------------------------------------------------
    # SSH key management
    # ---------------------------------------------------------------------------

    def get_or_create_ssh_key(
        self, public_key: str, is_file: bool = False
    ) -> ScalewaySSHKey:
        """Ensure an SSH key matching *public_key* is registered in the project.

        Scaleway injects the project's registered SSH keys into new Instances at
        boot, so there is no per-server key parameter; we register the key at the
        IAM/project level here.  The key name is the MD5 of the public key,
        matching the convention used by the other providers.
        """
        from scaleway.iam.v1alpha1 import IamV1Alpha1API

        if is_file:
            with open(public_key, "r", encoding="utf-8") as fh:
                public_key_str = fh.read().strip()
        else:
            public_key_str = public_key.strip()

        key_name = hashlib.md5(public_key_str.encode("utf-8")).hexdigest()
        iam = IamV1Alpha1API(self._client)

        for existing in iam.list_ssh_keys_all(project_id=self._project_id) or []:
            if (existing.public_key or "").strip() == public_key_str:
                return ScalewaySSHKey(name=existing.name, id=existing.id)

        with Action(f"Creating Scaleway SSH key {key_name}", stacklevel=3):
            created = iam.create_ssh_key(
                name=key_name,
                public_key=public_key_str,
                project_id=self._project_id,
            )
        return ScalewaySSHKey(name=created.name, id=created.id)

    # ---------------------------------------------------------------------------
    # Resource discovery
    # ---------------------------------------------------------------------------

    def get_server_type(self, name) -> ProviderServerType:
        """Validate and return a ProviderServerType for a canonical type name.

        *name* is the canonical dot-form (``dev1.s``).  We translate it to the
        native dash-form, validate it against the zone's available commercial
        types, and return a ProviderServerType whose ``.name`` stays canonical
        and whose ``._native`` carries the dash-form for the SDK.
        """
        canonical = canonical_type(name) if "-" in str(name) else str(name).lower()
        native = native_type(canonical)

        response = self._instance.list_servers_types(zone=self._zone)
        available = (getattr(response, "servers", None) or {})
        if native not in available:
            raise ServerTypeError(
                f"Scaleway server type '{canonical}' (native '{native}') not "
                f"available in zone {self._zone}"
            )
        return ProviderServerType(name=canonical, _native=native)

    def get_server_arch(self, server_type: ProviderServerType) -> str:
        """Return CPU architecture for *server_type* (canonical form)."""
        if _ARM64_RE.match(server_type.name):
            return "arm64"
        return "x64"

    def get_location(self, name, required: bool = False):
        """Validate and return the Scaleway zone string for *name*."""
        if name is None:
            if required:
                raise LocationError("Scaleway zone is not defined")
            return None
        zone = str(name).lower()
        if not _ZONE_RE.match(zone):
            raise LocationError(
                f"Scaleway zone '{name}' is not a valid zone (e.g. fr-par-1)"
            )
        return zone

    def get_image(self, image_spec):
        """Resolve a Scaleway image spec to an image identifier.

        Resolves, in order:

        1. an image **UUID** (custom or otherwise) -> returned as-is;
        2. a **marketplace label** such as ``ubuntu_jammy`` -> the zone-local
           image UUID (public base images);
        3. a **custom/private image name** -> the private image UUID for this
           project and zone (your own baked images, e.g. ``runner-base``).

        Foreign image specs (Hetzner's ``arch:type:name`` colon form, AWS
        ``ami-`` / ``resolve:ssm:`` specs) raise ``ImageSpecFormatError`` so a
        multi-cloud job can fall through to the provider that owns them.
        """
        import uuid as _uuid

        if image_spec is None:
            raise ImageError("Scaleway image spec is required")

        spec = str(image_spec).strip()

        # 1. UUID -> use directly.
        try:
            _uuid.UUID(spec)
            return spec
        except (ValueError, AttributeError):
            pass

        # Reject specs that clearly belong to another provider so scale_up can
        # try the next provider's get_image instead of hard-failing here.
        if ":" in spec or spec.startswith("ami-"):
            raise ImageSpecFormatError(
                f"'{spec}' is not a Scaleway image spec (expected an image UUID, "
                f"a marketplace label like 'ubuntu_jammy', or a custom image name)"
            )

        # 2. Marketplace label (public base images).
        marketplace_id = self._resolve_marketplace_image(spec)
        if marketplace_id is not None:
            return marketplace_id

        # 3. Custom/private image by name (case-insensitive; names may contain
        #    '-'/'.', which survive the label since get_server_image does not
        #    split the image value).
        custom_id = self._resolve_custom_image(spec)
        if custom_id is not None:
            return custom_id

        raise ImageError(
            f"Scaleway image '{spec}' not found in zone {self._zone}: no matching "
            f"marketplace label or custom image name"
        )

    def _resolve_marketplace_image(self, label: str) -> str | None:
        """Return the zone-local image UUID for a marketplace *label*, or None."""
        from scaleway.marketplace.v2 import MarketplaceV2API

        try:
            local_images = MarketplaceV2API(self._client).list_local_images_all(
                image_label=label,
                zone=self._zone,
                type_="instance_local",
            )
        except Exception as exc:
            raise ImageError(
                f"failed to query Scaleway marketplace for '{label}': {exc}"
            ) from exc
        return self._pick_image_id(local_images)

    def _resolve_custom_image(self, name: str) -> str | None:
        """Return the private image UUID matching *name* in this project, or None."""
        try:
            images = self._instance.list_images_all(
                zone=self._zone, name=name, public=False, project=self._project_id
            )
        except Exception as exc:
            raise ImageError(
                f"failed to query Scaleway custom images for '{name}': {exc}"
            ) from exc
        # The API name filter may match by prefix; require an exact (case-
        # insensitive) name match.
        matches = [
            img for img in (images or [])
            if (getattr(img, "name", "") or "").lower() == name.lower()
        ]
        return self._pick_image_id(matches)

    @staticmethod
    def _pick_image_id(images) -> str | None:
        """Pick an image id from *images*, preferring x86_64. None if empty."""
        if not images:
            return None
        x86 = [img for img in images if str(getattr(img, "arch", "")) == "x86_64"]
        return (x86[0] if x86 else images[0]).id
