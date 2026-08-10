"""Scaleway implementation of the CloudProvider interface.

Uses the official ``scaleway`` SDK (optional dependency:
``pip install testflows.runners[scaleway]``).

Recycling uses stop/start plus the configured recycle cleanup script. Scaleway
does not offer in-place image rebuild; a powered-off Instance releases its node
and may fail to power back on under capacity pressure, in which case the
candidate is terminated and fresh capacity is created on a later attempt.

Type translation: every type that crosses the SDK boundary is converted between
the canonical dot-form used by the orchestrator (``dev1.s``) and Scaleway's
native dash-form (``DEV1-S``) via :func:`utils.native_type` / :func:`utils.canonical_type`.
"""

import time
import hashlib
import logging
from dataclasses import replace
from datetime import datetime, timezone

from ...actions import Action
from ...cloud_provider import (
    AcquiredServer,
    CloudProvider,
    ProviderServer,
    ProviderServerType,
    RecycleClaim,
    RecycleRequest,
    RetirementResult,
)
from ...recycling import (
    activate_recycled_server,
    recyclable_server_matches,
    retire_to_recycle_pool,
)
from ...errors import ServerTypeError, ImageError, ImageSpecFormatError, LocationError
from .utils import (
    _RUNNER_TAG,
    _RUNNER_LABEL_TAG_PREFIX,
    _SSH_KEY_TAG,
    _ROOT_DISK_TAG,
    _RUNNER_VOLUME_TAG,
    _ACTIVE_STATES,
    canonical_type,
    native_type,
    tags_to_dict,
    dict_to_tags,
    state_key,
    _server_to_provider,
    _scaleway_error_type,
    normalize_public_key,
)
from .args import _ZONE_RE

# A detached SBS volume younger than this is left alone, so the reaper never
# races an in-flight create between volume creation and instance attachment.
_ORPHAN_VOLUME_GRACE_SECONDS = 120


class ScalewaySSHKey:
    """Minimal SSH-key descriptor returned by ``get_or_create_ssh_key``."""

    def __init__(self, name: str, id: str = None):
        self.name = name
        self.id = id


class ScalewayCloudProvider(CloudProvider):
    """Scaleway Instances implementation of CloudProvider.

    Recycling uses stop/start without image rebuild. Volume operations raise
    ``NotImplementedError`` (inherited from base class).
    """

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        project_id: str,
        organization_id: str = None,
        zone: str = "fr-par-1",
        zones: list[str] = None,
        default_image_spec: str = None,
        default_location_spec: str = None,
        default_server_type_spec: str = None,
        default_volume_size: int = None,
        ssh_user: str = "root",
        max_runners: int = None,
        end_of_life: int = None,
        recycle: bool = None,
        recycle_grace_period: int = None,
    ):
        from scaleway import Client
        from scaleway.instance.v1 import InstanceV1API
        from scaleway.block.v1 import BlockV1API

        self._client = Client(
            access_key=access_key,
            secret_key=secret_key,
            default_project_id=project_id,
            default_organization_id=organization_id,
            default_zone=zone,
        )
        self._instance = InstanceV1API(self._client)
        self._block = BlockV1API(self._client)
        self._project_id = project_id
        self._organization_id = organization_id
        self._zone = zone
        # Zones this provider operates over (listing/prices/fallback). Derived
        # from in- labels by the factory; filtered to valid Scaleway zones here.
        self._zones = set()
        for z in zones or []:
            try:
                if self.get_location(z) is not None:
                    self._zones.add(z)
            except LocationError:
                pass
        self._zones.add(zone)
        self._default_image = default_image_spec
        self._default_location = default_location_spec
        self._default_server_type = default_server_type_spec
        # Configured boot-volume size in GB (providers.scaleway.defaults.volume_size).
        self._default_volume_size = default_volume_size
        self._ssh_user = ssh_user
        self._max_runners = max_runners
        self._end_of_life = end_of_life
        self._recycle = recycle
        self._recycle_grace_period = recycle_grace_period

    # ---------------------------------------------------------------------------
    # Identity
    # ---------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "scaleway"

    @property
    def currency(self) -> str:
        return "EUR"

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
            if state_key(server.state) in states:
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
        public_net=None,
        root_disk_size: int = None,
    ) -> ProviderServer:
        """Create a Scaleway Instance and power it on, in one of two boot modes.

        The mode is auto-detected from the instance type's storage capability
        (see ``_is_local_bootable``); there is no config flag, because one
        provider instance serves both runner orchestration and the controller
        deploy, so the choice must be per-call:

        * **LOCAL** (local-storage-capable types, e.g. a small controller host):
          launch with ``image=`` and no volumes so the instance API provisions a
          local ``l_ssd`` boot volume — included in the instance price, deleted
          on terminate, no block storage. Used by ``cloud deploy`` for the
          controller (small local type + marketplace image).
        * **SBS** (SBS-only types, e.g. ARM/BASIC2 runners): pre-create a tagged
          SBS boot volume from the image's root snapshot and attach it by id, so
          the ``after_scale_down`` reaper can reclaim it (there is no untagged
          window). This is the runner path and the default for a bare
          ``ProviderServerType`` (no ``_native``).

        ``ssh_keys`` is accepted for interface compatibility but not passed to
        the API (Scaleway injects the project's registered SSH keys at boot; see
        ``get_or_create_ssh_key``).  ``volumes``/``public_net`` are ignored (the
        boot volume is derived from the image). ``root_disk_size`` (GB), when
        given, sizes the SBS boot volume for this server instead of the
        configured default; it does not apply to LOCAL boot (the local disk is
        provisioned by the instance API — the caller already verified the type's
        local capacity via ``fixed_root_disk``).
        """
        del ssh_keys, volumes, public_net

        zone = location or self._zone
        image = self._resolve_image_in_zone(image, zone)
        commercial_type = native_type(server_type.name)

        if self._is_local_bootable(server_type):
            # LOCAL boot: let the instance API provision the (local) boot volume
            # from the image. No block pre-create, no reaper tag, and marketplace
            # images work here (the block-API SBS-from-marketplace path 403s).
            created = self._instance._create_server(
                zone=zone,
                name=name,
                commercial_type=commercial_type,
                dynamic_ip_required=True,
                protected=False,
                tags=dict_to_tags(labels),
                project=self._project_id,
                image=image,
            )
        else:
            from scaleway.instance.v1 import VolumeServerTemplate, VolumeVolumeType

            boot_volume_name = f"{name}-boot"[:60]
            # Grow the boot volume to the job's requested minimum (disk- label)
            # or the configured default size (GB -> bytes; Scaleway sizes are
            # binary GiB). The image snapshot size is the floor.
            _boot_gb = root_disk_size or self._default_volume_size
            requested_size = _boot_gb * 1024**3 if _boot_gb else None
            boot_volume_id = self._create_boot_volume(
                image_uuid=image,
                zone=zone,
                name=boot_volume_name,
                tags=[_RUNNER_VOLUME_TAG],
                size=requested_size,
            )
            # Volume-first create: attach the tagged SBS boot volume by id and
            # omit ``image`` (the volume already carries the image contents).
            # Record the boot size so the recycle disk-safety gate can read it
            # back (SBS size is absent from the Instance API listing).
            sbs_labels = (
                {**labels, _ROOT_DISK_TAG: str(_boot_gb)} if _boot_gb else labels
            )
            created = self._instance._create_server(
                zone=zone,
                name=name,
                commercial_type=commercial_type,
                dynamic_ip_required=True,
                protected=False,
                tags=dict_to_tags(sbs_labels),
                project=self._project_id,
                volumes={
                    # size/name are create-time fields; the SDK defaults size to
                    # 0 (not None), and sending it alongside id makes Scaleway
                    # reject the request ("cannot specify 'id' and 'size'"), so
                    # size must be None here.
                    "0": VolumeServerTemplate(
                        id=boot_volume_id,
                        boot=True,
                        volume_type=VolumeVolumeType.SBS_VOLUME,
                        size=None,
                    )
                },
            )

        server = self._power_on_and_wait(created.server, zone, name)
        return _server_to_provider(server, ssh_user=self._ssh_user)

    def fixed_root_disk(self, server_type) -> int | None:
        """Local-boot types have a fixed local disk capped by ``l_ssd.max_size``.

        For a local-bootable type the root disk is the local SSD, whose size is
        bounded by the type's ``per_volume_constraint.l_ssd.max_size`` (bytes) —
        returned here in GB so a ``disk-`` minimum larger than the type can hold
        rejects it during resolution. SBS types have a user-sized boot volume
        (resizable), so return None for them.
        """
        native = getattr(server_type, "_native", None)
        pvc = getattr(native, "per_volume_constraint", None)
        l_ssd = getattr(pvc, "l_ssd", None) if pvc else None
        max_size = getattr(l_ssd, "max_size", 0) or 0
        if not max_size:
            return None
        return int(max_size // (1024**3))

    @staticmethod
    def _is_local_bootable(server_type) -> bool:
        """Whether *server_type* has local (l_ssd) storage for a local boot volume.

        SBS-only types report ``per_volume_constraint.l_ssd.max_size == 0`` (or no
        l_ssd constraint). A bare ``ProviderServerType`` with no ``_native`` (the
        orchestrator's default and the test default) is treated as SBS, so the
        SBS path stays the default and only an explicitly local-capable type (from
        ``get_server_type``) selects local boot.
        """
        native = getattr(server_type, "_native", None)
        pvc = getattr(native, "per_volume_constraint", None)
        l_ssd = getattr(pvc, "l_ssd", None) if pvc else None
        return bool(getattr(l_ssd, "max_size", 0) or 0)

    def _power_on_and_wait(self, server, zone, name):
        """Power on *server* and wait until running; remove it on failure.

        An out-of-stock POWERON leaves the instance ``stopped`` (created but never
        booted), so cleanup must go through delete_server, which removes a stopped
        instance via the DELETE endpoint — a plain terminate is rejected for it.
        The removal is best-effort (logged, never masks the original error); the
        tagged SBS boot volume is reclaimed by the after_scale_down reaper.
        """
        from scaleway.instance.v1 import ServerAction

        try:
            self._instance.server_action(
                server_id=server.id, zone=zone, action=ServerAction.POWERON
            )
            with Action(f"Waiting for Scaleway instance {name} to start", stacklevel=3):
                server = self._wait_for_state(
                    server.id, zone, states={"running"}, timeout=300
                )
        except Exception:
            with Action(
                f"Removing {name} after it failed to start",
                stacklevel=3,
                ignore_fail=True,
            ):
                self.delete_server(_server_to_provider(server, ssh_user=self._ssh_user))
            raise

        return server

    def delete_server(self, server: ProviderServer) -> None:
        """Remove the Instance; the tagged SBS boot volume is reaped out of band.

        Two API operations, each rejected in the state the other handles:

        * ``terminate`` removes a *running* / ``stopped_in_place`` instance (still
          holding its reservation) but is rejected for a fully powered-off one.
        * the DELETE endpoint removes a fully ``stopped`` instance but is rejected
          while it still holds resources ("instance should be powered off").

        ``status`` collapses ``stopped`` and ``stopped_in_place`` into ``OFF``, so
        pick the op from the raw state instead. No precondition fallback for now:
        if the observed state is stale we want the mispick to surface rather than
        silently self-correct, while we characterize why instances end up stopped.
        Either op only detaches (not deletes) the SBS boot volume, reclaimed later
        by ``after_scale_down`` reaping.
        """
        from scaleway.instance.v1 import ServerAction

        raw = state_key(getattr(getattr(server, "_native", None), "state", ""))
        if raw == "stopped":
            self._instance.delete_server(server_id=server.id, zone=server.location)
        else:
            self._instance.server_action(
                server_id=server.id, zone=server.location,
                action=ServerAction.TERMINATE,
            )

    def _create_boot_volume(
        self,
        image_uuid: str,
        zone: str,
        name: str,
        tags: list,
        size: int | None = None,
    ) -> str:
        """Create a tagged SBS boot volume from an image's root snapshot.

        The volume is tagged *at creation*, so the ``after_scale_down`` reaper
        can always reclaim it — this closes the untagged window the old
        create-then-tag flow left open. Returns the new volume id.

        Shaped for reuse by a future ``rebuild_server``: ``tags`` and ``size``
        are parameters rather than hardcoded.

        Only own-project SBS images work. A marketplace/public image's root
        snapshot lives in another project, and the Block API denies creating a
        volume from it, so we surface a helpful ``ImageError``.
        """
        from scaleway.block.v1 import CreateVolumeRequestFromSnapshot
        from scaleway_core.api import ScalewayException

        response = self._instance.get_image(image_id=image_uuid, zone=zone)
        image = getattr(response, "image", response)
        root_volume = getattr(image, "root_volume", None)
        if root_volume is None or (
            str(getattr(root_volume, "volume_type", "")).lower() != "sbs_snapshot"
        ):
            raise ImageError(
                f"Scaleway image {image_uuid!r} has no SBS snapshot root volume; "
                f"only SBS custom images are supported. Bake an SBS image in your "
                f"project (local or marketplace images cannot be used)."
            )

        # A from_snapshot volume cannot be smaller than its snapshot, so use the
        # snapshot size as the floor and grow to the requested ``size`` when it
        # is larger. root_volume.size is unreliable (reports 0), so read the real
        # size from the snapshot. A cross-project (marketplace) snapshot is not
        # readable — fall through and let create_volume surface the 403 below.
        snapshot_size = None
        try:
            snapshot = self._block.get_snapshot(
                snapshot_id=root_volume.id, zone=zone
            )
            snapshot_size = getattr(snapshot, "size", None)
        except ScalewayException:
            snapshot_size = None
        volume_size = max([s for s in (size, snapshot_size) if s], default=None)

        try:
            volume = self._block.create_volume(
                zone=zone,
                name=name,
                project_id=self._project_id,
                tags=list(tags),
                from_snapshot=CreateVolumeRequestFromSnapshot(
                    snapshot_id=root_volume.id, size=volume_size
                ),
            )
        except ScalewayException as exc:
            # Scaleway returns HTTP 403 for BOTH a cross-project snapshot
            # (permissions_denied) and a full quota (quotas_exceeded), so the
            # response body's ``type`` — not the status code — is what tells them
            # apart. Only a genuine permission denial is an image-config problem;
            # a quota exhaustion is a transient capacity condition, so let it
            # propagate as an ordinary create failure (scale_up retries; the
            # after_scale_down reaper frees SBS volumes to make room).
            if _scaleway_error_type(exc) == "permissions_denied":
                raise ImageError(
                    f"Scaleway denied creating a volume from image {image_uuid!r}: "
                    f"its root snapshot is not in your project (a marketplace or "
                    f"public image). Bake the image into your own project as an SBS "
                    f"custom image and reference that instead."
                ) from exc
            raise

        with Action(
            f"Waiting for Scaleway boot volume {volume.id} to be ready",
            stacklevel=3,
        ):
            self._block.wait_for_volume(volume_id=volume.id, zone=zone)
        return volume.id

    def after_scale_down(self) -> None:
        """Run provider maintenance after scale-down decisions."""
        self._reap_orphaned_volumes()

    def _reap_orphaned_volumes(self) -> None:
        """Delete detached SBS volumes we created that no instance references.

        terminate detaches (does not delete) boot-on-block volumes, so they
        linger and consume the SbsVolumeSizeGb quota. Each is tagged at create
        time; here we delete any that are detached (no references) and have been
        detached longer than a short grace, so we never race an in-flight
        create. Stateless and idempotent — safe to run every scale_down cycle.
        """
        try:
            volumes = self._block.list_volumes_all(
                zone=self._zone, tags=[_RUNNER_VOLUME_TAG], include_deleted=False
            )
        except Exception as exc:
            with Action(
                f"Could not list Scaleway volumes to reap: {exc}",
                stacklevel=3,
                ignore_fail=True,
            ):
                pass
            return

        now = datetime.now(timezone.utc)
        for vol in volumes or []:
            if getattr(vol, "references", None):
                continue  # still attached to an instance
            detached_at = getattr(vol, "last_detached_at", None) or getattr(
                vol, "created_at", None
            )
            if (
                detached_at is not None
                and (now - detached_at).total_seconds() < _ORPHAN_VOLUME_GRACE_SECONDS
            ):
                continue  # too fresh; avoid racing an in-flight create
            try:
                self._block.delete_volume(volume_id=vol.id, zone=self._zone)
                with Action(
                    f"Reaped orphaned Scaleway volume {vol.id}",
                    stacklevel=3,
                    level=logging.DEBUG,
                ):
                    pass
            except Exception as exc:
                if getattr(exc, "status_code", None) == 404:
                    continue
                with Action(
                    f"Could not reap orphaned volume {vol.id}: {exc}",
                    stacklevel=3,
                    ignore_fail=True,
                ):
                    pass

    def get_server(self, name: str) -> ProviderServer | None:
        servers = self._instance.list_servers_all(zone=self._zone, name=name)
        for server in servers or []:
            if server.name == name and state_key(server.state) in _ACTIVE_STATES:
                return _server_to_provider(server, ssh_user=self._ssh_user)
        return None

    def list_servers(self, label_selector: str = None) -> list[ProviderServer]:
        """List Instances, optionally filtered by a ``key=value`` tag selector."""
        tags = [label_selector] if label_selector and "=" in label_selector else None
        servers = self._instance.list_servers_all(zone=self._zone, tags=tags)
        return [
            _server_to_provider(s, ssh_user=self._ssh_user)
            for s in (servers or [])
            if state_key(s.state) in _ACTIVE_STATES
        ]

    def power_off_server(self, server: ProviderServer) -> None:
        from scaleway.instance.v1 import ServerAction

        # stop_in_place, not poweroff: poweroff releases the compute reservation
        # (UI "archived") and ends the paid hour, which defeats recycling. Stop
        # in place keeps the instance on its hypervisor for a fast, in-hour reuse.
        self._instance.server_action(
            server_id=server.id, zone=server.location, action=ServerAction.STOP_IN_PLACE
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

    def is_recycled_server(self, server: ProviderServer) -> bool:
        from ...constants import recycle_server_name_prefix

        return server.name.startswith(recycle_server_name_prefix)

    def claim_recycled_server(self, request: RecycleRequest) -> RecycleClaim | None:
        return self._claim_matching_recycled_server(
            request,
            lambda server, req: recyclable_server_matches(
                self,
                server,
                replace(
                    req,
                    enable_ipv4=bool(server.public_ipv4),
                    enable_ipv6=bool(server.public_ipv6),
                ),
                # Scaleway never reimages on recycle, so the image must match.
                require_image_match=True,
            ),
        )

    def is_runner_label_tag(self, key: str) -> bool:
        return key.startswith(_RUNNER_LABEL_TAG_PREFIX)

    def activate_recycled_server(
        self, claim: RecycleClaim
    ) -> AcquiredServer | None:
        return activate_recycled_server(
            self,
            claim,
            rebuild=False,
            delete_on_activation_failure=True,
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
        return {
            value.lower()
            for key, value in server.labels.items()
            if key.startswith(_RUNNER_LABEL_TAG_PREFIX)
        }

    def has_matching_ssh_key(
        self, server: ProviderServer, ssh_key_names: set[str]
    ) -> bool:
        key_name = server.labels.get(_SSH_KEY_TAG)
        return key_name in ssh_key_names if key_name is not None else False

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

        # Reuse an existing key rather than create a duplicate, matching on key
        # *identity* (type + base64 blob), not name or the raw string. A raw
        # compare trips on a differing trailing comment, and the name won't match
        # a key registered out of band under another name (e.g. a human's
        # personal key with the same material) — either miss creates a duplicate
        # every startup and exhausts the org-wide IamSshKeys quota. The
        # normalized blob is the same identity a fingerprint hashes, so it
        # matches the same key regardless of comment or registered name.
        target_identity = normalize_public_key(public_key_str)
        for existing in iam.list_ssh_keys_all(project_id=self._project_id) or []:
            if (
                existing.name == key_name
                or normalize_public_key(existing.public_key) == target_identity
            ):
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
        # Store the SDK ServerType (it carries the authoritative ``arch``) so
        # get_server_arch does not have to guess from the name.
        return ProviderServerType(name=canonical, _native=available[native])

    def get_server_arch(self, server_type: ProviderServerType) -> str:
        """Return CPU architecture ('arm64' or 'x64') for *server_type*.

        Uses the authoritative ``arch`` from the SDK ServerType stored in
        ``_native`` (Scaleway reports 'arm'/'arm64'/'x86_64'). Types resolved via
        ``get_server_type`` always carry it; a bare ProviderServerType with no
        SDK object defaults to x64.
        """
        native_arch = str(getattr(server_type._native, "arch", "") or "").lower()
        return "arm64" if native_arch.startswith("arm") else "x64"

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
        """Validate a Scaleway image spec for multi-cloud fallthrough.

        Returns the spec unchanged (no API call, no name validation — Scaleway
        arbitrates valid names). Rejects clearly-foreign specs so scale_up can
        try the next provider. Actual resolution happens per target zone in
        create_server via _resolve_image_in_zone.
        """
        if image_spec is None:
            raise ImageError("Scaleway image spec is required")
        spec = str(image_spec).strip()
        if ":" in spec or spec.startswith("ami-"):
            raise ImageSpecFormatError(
                f"'{spec}' is not a Scaleway image spec (expected an image UUID, "
                f"a marketplace label like 'ubuntu_jammy', or a custom image name)"
            )
        return spec

    def _resolve_image_in_zone(self, spec: str, zone: str) -> str:
        """Resolve a Scaleway image spec to a zone-local image UUID.

        UUID passthrough -> marketplace label -> custom image name, all in the
        given zone. Raises ImageError if nothing matches in that zone.
        """
        import uuid as _uuid

        try:
            _uuid.UUID(spec)
            return spec
        except (ValueError, AttributeError):
            pass
        if spec.replace("_", "").isalnum():
            marketplace_id = self._resolve_marketplace_image(spec, zone)
            if marketplace_id is not None:
                return marketplace_id
        custom_id = self._resolve_custom_image(spec, zone)
        if custom_id is not None:
            return custom_id
        raise ImageError(
            f"Scaleway image '{spec}' not found in zone {zone}: no matching "
            f"marketplace label or custom image name"
        )

    def _resolve_marketplace_image(self, label: str, zone: str) -> str | None:
        """Return the zone-local image UUID for a marketplace *label*, or None.

        Returns None (rather than raising) when the label is not a known
        marketplace image, so ``_resolve_image_in_zone`` can fall through to
        custom images.
        """
        from scaleway.marketplace.v2 import MarketplaceV2API
        from scaleway_core.api import ScalewayException

        try:
            local_images = MarketplaceV2API(self._client).list_local_images_all(
                image_label=label,
                zone=zone,
                type_="instance_local",
            )
        except ScalewayException as exc:
            if getattr(exc, "status_code", None) == 404:
                return None  # not a marketplace label; try custom images
            raise ImageError(
                f"failed to query Scaleway marketplace for '{label}': {exc}"
            ) from exc
        return self._pick_image_id(local_images)

    def _resolve_custom_image(self, name: str, zone: str) -> str | None:
        """Return the private image UUID matching *name* in this project, or None."""
        try:
            images = self._instance.list_images_all(
                zone=zone, name=name, public=False, project=self._project_id
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
