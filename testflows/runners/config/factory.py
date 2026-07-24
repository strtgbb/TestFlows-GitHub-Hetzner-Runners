"""Provider factory: construct CloudProvider instances from Config."""

from .config import Config
from ..cloud_provider import CloudProvider


def provider_factory(config: Config) -> list[CloudProvider]:
    """Construct and return all configured CloudProvider instances.

    Args:
        config: Populated Config object.

    Returns:
        List of CloudProvider instances in configuration (precedence) order.
    """
    from ..providers.hetzner.provider import HetznerCloudProvider

    providers: list[CloudProvider] = []

    if config.providers.hetzner and config.providers.hetzner.token:
        hetzner_defaults = config.providers.hetzner.defaults
        providers.append(
            HetznerCloudProvider(
                token=config.providers.hetzner.token,
                ssh_key_path=config.ssh_key,
                default_image=hetzner_defaults.image,
                default_server_type=hetzner_defaults.server_type,
                default_location=hetzner_defaults.location,
                default_volume_size=hetzner_defaults.volume_size,
                default_volume_location=hetzner_defaults.volume_location,
                max_runners=config.providers.hetzner.max_runners,
                end_of_life=config.providers.hetzner.end_of_life,
                recycle=config.providers.hetzner.recycle,
                recycle_grace_period=config.providers.hetzner.recycle_grace_period,
                recycle_with_rebuild=config.providers.hetzner.recycle_with_rebuild,
            )
        )

    # Provider precedence (Scaleway ahead of AWS, matching provider_list field
    # order): the first configured provider seeds default type/location/volume
    # for jobs with no type/location label.
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
                default_server_type_spec=scaleway_cfg.defaults.server_type,
                default_volume_size=scaleway_cfg.defaults.volume_size,
                ssh_user=scaleway_cfg.ssh_user,
                max_runners=scaleway_cfg.max_runners,
                end_of_life=scaleway_cfg.end_of_life,
                recycle=scaleway_cfg.recycle,
                recycle_grace_period=scaleway_cfg.recycle_grace_period,
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
                default_server_type_spec=aws_cfg.defaults.server_type,
                ssh_user=aws_cfg.ssh_user,
                root_volume_size=aws_cfg.defaults.volume_size,
                root_volume_type=aws_cfg.defaults.volume_type,
                max_runners=aws_cfg.max_runners,
                end_of_life=aws_cfg.end_of_life,
                recycle=aws_cfg.recycle,
                recycle_grace_period=aws_cfg.recycle_grace_period,
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
