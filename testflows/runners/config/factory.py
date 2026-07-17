"""Provider factory: construct CloudProvider instances from Config."""

import dataclasses
import logging

from .config import (
    Config,
    hetzner_provider as HetznerProviderConfig,
    aws_provider as AWSProviderConfig,
)
from ..cloud_provider import CloudProvider

logger = logging.getLogger("testflows.runners")


def provider_factory(config: Config) -> list[CloudProvider]:
    """Construct and return all configured CloudProvider instances.

    Handles the backwards-compatible ``hetzner_token`` flat field: if it is
    set and no ``providers.hetzner.token`` has been supplied, the token is
    synced into ``config.providers.hetzner`` with a deprecation warning.

    Args:
        config: Populated Config object.

    Returns:
        List of CloudProvider instances in configuration order.
    """
    from ..providers.hetzner.provider import HetznerCloudProvider

    # Backwards compat: hetzner_token → providers.hetzner.token
    # Only auto-wire if the user has not configured another provider via the new
    # providers block. If they have (e.g. providers.aws or providers.scaleway),
    # the env var HETZNER_TOKEN is ambient noise and should not silently create
    # a Hetzner provider.
    if config.hetzner_token:
        if config.providers.hetzner is None or not config.providers.hetzner.token:
            has_explicit_aws = config.providers.aws is not None and bool(
                config.providers.aws.access_key_id
            )
            has_explicit_scaleway = config.providers.scaleway is not None and bool(
                config.providers.scaleway.access_key
            )
            has_explicit_dedicated_static = (
                config.providers.dedicated_static is not None
                and bool(config.providers.dedicated_static.groups)
            )
            has_explicit_provider = (
                has_explicit_aws
                or has_explicit_scaleway
                or has_explicit_dedicated_static
            )
            if not has_explicit_provider:
                logger.warning(
                    "hetzner_token is deprecated; use providers.hetzner.token instead"
                )
                if config.providers.hetzner is None:
                    config.providers.hetzner = HetznerProviderConfig(
                        token=config.hetzner_token
                    )
                else:
                    config.providers.hetzner = dataclasses.replace(
                        config.providers.hetzner, token=config.hetzner_token
                    )

    providers: list[CloudProvider] = []

    if config.providers.hetzner and config.providers.hetzner.token:
        providers.append(
            HetznerCloudProvider(
                token=config.providers.hetzner.token,
                ssh_key_path=config.ssh_key,
                max_runners=config.providers.hetzner.max_runners,
                end_of_life=config.providers.hetzner.end_of_life,
                recycle_with_rebuild=config.providers.hetzner.recycle_with_rebuild,
            )
        )

    aws_cfg = config.providers.aws
    if aws_cfg and aws_cfg.access_key_id and aws_cfg.secret_access_key:
        from ..providers.aws.provider import AWSCloudProvider
        from ..providers.aws.utils import _az_to_region

        location = aws_cfg.defaults.location or "us-east-1a"
        region = _az_to_region(location)
        providers.append(
            AWSCloudProvider(
                access_key_id=aws_cfg.access_key_id,
                secret_access_key=aws_cfg.secret_access_key,
                region=region,
                security_group=aws_cfg.security_group,
                subnets=aws_cfg.subnets,
                default_image_spec=aws_cfg.defaults.image,
                default_location_spec=aws_cfg.defaults.location,
                ssh_user=aws_cfg.ssh_user,
                root_volume_size=aws_cfg.defaults.volume_size,
                root_volume_type=aws_cfg.defaults.volume_type,
                max_runners=aws_cfg.max_runners,
                end_of_life=aws_cfg.end_of_life,
            )
        )

    scaleway_cfg = config.providers.scaleway
    if (
        scaleway_cfg
        and scaleway_cfg.access_key
        and scaleway_cfg.secret_key
        and scaleway_cfg.project_id
    ):
        from ..providers.scaleway.provider import ScalewayCloudProvider

        providers.append(
            ScalewayCloudProvider(
                access_key=scaleway_cfg.access_key,
                secret_key=scaleway_cfg.secret_key,
                project_id=scaleway_cfg.project_id,
                organization_id=scaleway_cfg.organization_id,
                zone=scaleway_cfg.defaults.location or "fr-par-1",
                default_image_spec=scaleway_cfg.defaults.image,
                default_location_spec=scaleway_cfg.defaults.location,
                ssh_user=scaleway_cfg.ssh_user,
                max_runners=scaleway_cfg.max_runners,
                end_of_life=scaleway_cfg.end_of_life,
            )
        )

    dedicated_cfg = config.providers.dedicated_static
    if dedicated_cfg and dedicated_cfg.groups:
        from ..providers.dedicated_static.provider import DedicatedStaticCloudProvider

        groups = {}
        for group_name, group in dedicated_cfg.groups.items():
            ssh_user = (
                group.ssh.user
                if group.ssh is not None
                else dedicated_cfg.ssh_defaults.user
            )
            ssh_port = (
                group.ssh.port
                if group.ssh is not None
                else dedicated_cfg.ssh_defaults.port
            )
            ssh_key_path = (
                group.ssh.key
                if group.ssh is not None and group.ssh.key
                else dedicated_cfg.ssh_defaults.key
            )
            groups[group_name] = {
                "labels": group.labels,
                "hosts": group.hosts,
                "ssh_user": ssh_user,
                "ssh_port": ssh_port,
                "ssh_key_path": ssh_key_path,
            }

        providers.append(
            DedicatedStaticCloudProvider(
                groups=groups,
                default_ssh_user=dedicated_cfg.ssh_defaults.user,
                claim_ttl_minutes=dedicated_cfg.claim_ttl_minutes,
                # Routing labels (type-/in-) carry the global label_prefix.
                label_prefix=config.label_prefix,
            )
        )

    return providers
