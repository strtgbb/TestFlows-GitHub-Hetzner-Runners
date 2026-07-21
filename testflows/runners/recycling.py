"""Shared mechanics used by providers with a stopped-server recycle pool."""

import time
import uuid
from datetime import datetime, timezone

from .cloud_provider import (
    AcquiredServer,
    CloudProvider,
    ProviderServer,
    RecycleClaim,
    RecycleRequest,
    RetirementResult,
)
from .constants import (
    recycle_image_label,
    recycle_server_name_prefix,
    recycle_timestamp_label,
)


def recyclable_server_matches(
    provider: CloudProvider,
    server: ProviderServer,
    request: RecycleRequest,
    *,
    require_image_match: bool = True,
) -> bool:
    """Return whether a provider server satisfies a recycle request.

    ``require_image_match`` is False only for providers that reimage the server
    on activation (the reused disk is overwritten, so its original image is
    irrelevant). The provider decides this and passes it in; it is not part of
    the public provider interface.
    """
    if not provider.is_recycled_server(server):
        return False
    if server.status != CloudProvider.STATUS_OFF:
        return False
    if server.server_type != request.server_type:
        return False
    if request.location and server.location != request.location:
        return False
    if bool(server.public_ipv4) != request.enable_ipv4:
        return False
    if bool(server.public_ipv6) != request.enable_ipv6:
        return False
    if not provider.has_matching_ssh_key(server, set(request.ssh_key_names)):
        return False
    if require_image_match:
        if provider.get_server_tag(server, recycle_image_label) != request.labels.get(
            recycle_image_label
        ):
            return False

    if len(server.volumes) != len(request.volume_names):
        return False
    return all(
        any(
            volume.name == name or volume.name.startswith(f"{name}-")
            for volume in server.volumes
        )
        for name in request.volume_names
    )


def activate_recycled_server(
    provider: CloudProvider,
    claim: RecycleClaim,
    *,
    rebuild: bool,
    delete_on_activation_failure: bool = False,
) -> AcquiredServer | None:
    """Activate a claimed server, committing its new identity only on success."""
    request = claim.request
    server = provider.get_server(claim.server.name)
    if server is None:
        provider.release_recycle_claim(claim)
        return None

    try:
        try:
            if rebuild:
                provider.rebuild_server(server, request.image)
            else:
                provider.power_on_server(server, timeout=request.timeout)
        except Exception:
            if delete_on_activation_failure:
                provider.delete_server(server)
                provider.mark_recycled_server_deleting(server)
                return None
            raise

        labels = provider.labels_for_recycled_server(server, request.labels)
        labels.pop(recycle_timestamp_label, None)
        valid, error = provider.validate_labels(labels)
        if not valid:
            raise ValueError(f"invalid server labels {labels}: {error}")
        provider.update_server(server, name=request.name, labels=labels)
        return AcquiredServer(
            server=server,
            use_recycle_script=not rebuild,
        )
    except Exception:
        if delete_on_activation_failure:
            provider.delete_server(server)
            provider.mark_recycled_server_deleting(server)
        else:
            try:
                current = provider.get_server(server.name) or server
                provider.power_off_server(current)
            except Exception:
                pass
        raise
    finally:
        provider.release_recycle_claim(claim)


def retire_to_recycle_pool(
    provider: CloudProvider,
    server: ProviderServer,
    *,
    recycle_enabled: bool,
    ssh_key_names: set[str],
    end_of_life: int,
    recycle_grace_period: int,
) -> RetirementResult:
    """Park, retain, or delete a server in a reusable cloud pool."""
    original_name = server.name
    if not provider.has_matching_ssh_key(server, ssh_key_names):
        return RetirementResult("unmanaged", original_name)
    if not recycle_enabled:
        provider.delete_server(server)
        return RetirementResult("deleted", original_name)

    if provider.is_recycle_claimed(server):
        return RetirementResult("claimed", original_name)

    created = server.created
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    minutes = int((datetime.now(timezone.utc) - created).total_seconds() // 60) % 60
    now = int(time.time())

    if provider.is_recycled_server(server):
        if server.status != CloudProvider.STATUS_OFF:
            provider.power_off_server(server)
        recycle_timestamp = provider.get_server_tag(server, recycle_timestamp_label)
        try:
            recycle_timestamp = int(recycle_timestamp)
        except (TypeError, ValueError):
            recycle_timestamp = 0

        if recycle_timestamp <= 0:
            provider.set_server_tags(
                server, {recycle_timestamp_label: str(now)}
            )
            return RetirementResult("pooled", original_name)

        if minutes >= end_of_life:
            if now - recycle_timestamp >= recycle_grace_period:
                if not provider.reserve_recycled_server(server):
                    return RetirementResult("claimed", original_name)
                try:
                    provider.delete_server(server)
                except Exception:
                    provider.release_recycled_server(server)
                    raise
                provider.mark_recycled_server_deleting(server)
                return RetirementResult("deleted", original_name)
        return RetirementResult("pooled", original_name)

    if server.status != CloudProvider.STATUS_OFF:
        provider.power_off_server(server)
    labels = dict(server.labels)
    labels[recycle_timestamp_label] = str(now)
    provider.update_server(
        server,
        name=f"{recycle_server_name_prefix}{uuid.uuid4().hex[:12]}",
        labels=labels,
    )
    return RetirementResult("pooled", original_name)
