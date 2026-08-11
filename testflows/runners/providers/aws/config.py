"""AWS provider configuration."""

from ...config.config import aws_provider, provider_defaults


def is_enabled(provider_config):
    """Check if AWS provider is enabled (has required credentials)."""
    return (
        provider_config
        and provider_config.access_key_id
        and provider_config.secret_access_key
    )


def get_cli_fields():
    """Get list of all CLI field names for AWS provider."""
    return [
        "access_key_id",
        "secret_access_key",
        "security_group",
        "subnets",
        "key_name",
        "default_image",
        "default_server_type",
        "default_location",
        "default_volume_size",
        "default_volume_location",
        "default_volume_type",
    ]


def has_cli_args(args):
    """Check if any AWS CLI arguments are provided."""
    return any(
        getattr(args, f"aws_{field}", None) is not None for field in get_cli_fields()
    )


def update_from_args(provider_config, args):
    """Update AWS provider configuration from CLI arguments."""
    if not provider_config:
        return

    # Update credentials
    if getattr(args, "aws_access_key_id", None) is not None:
        provider_config.access_key_id = args.aws_access_key_id
    if getattr(args, "aws_secret_access_key", None) is not None:
        provider_config.secret_access_key = args.aws_secret_access_key
    if getattr(args, "aws_security_group", None) is not None:
        provider_config.security_group = args.aws_security_group
    if getattr(args, "aws_subnets", None) is not None:
        provider_config.subnets = args.aws_subnets
    if getattr(args, "aws_key_name", None) is not None:
        provider_config.key_name = args.aws_key_name

    # Update defaults
    if getattr(args, "aws_default_image", None) is not None:
        provider_config.defaults.image = args.aws_default_image
    if getattr(args, "aws_default_server_type", None) is not None:
        provider_config.defaults.server_type = args.aws_default_server_type
    if getattr(args, "aws_default_location", None) is not None:
        provider_config.defaults.location = args.aws_default_location
    if getattr(args, "aws_default_volume_size", None) is not None:
        provider_config.defaults.volume_size = args.aws_default_volume_size
    if getattr(args, "aws_default_volume_location", None) is not None:
        provider_config.defaults.volume_location = args.aws_default_volume_location
    if getattr(args, "aws_default_volume_type", None) is not None:
        provider_config.defaults.volume_type = args.aws_default_volume_type


# AWS-specific validation functions


def parse_config_section(section: dict) -> "aws_provider":
    """Validate and coerce a ``providers.aws`` config section into an
    ``aws_provider`` dataclass. Moved from config/parse.py verbatim.
    """
    a = section
    assert isinstance(a, dict), "config.providers.aws: is not a dictionary"
    if a.get("access_key_id") is not None:
        assert isinstance(
            a["access_key_id"], str
        ), "config.providers.aws.access_key_id: is not a string"
    if a.get("secret_access_key") is not None:
        assert isinstance(
            a["secret_access_key"], str
        ), "config.providers.aws.secret_access_key: is not a string"
    _subnets_raw = a.get("subnets")
    if _subnets_raw is not None:
        if isinstance(_subnets_raw, str):
            _subnets_raw = [_subnets_raw]
        assert isinstance(_subnets_raw, list) and all(
            isinstance(s, str) for s in _subnets_raw
        ), "config.providers.aws.subnets: must be a string or list of strings"
    _aws_kwargs = dict(
        access_key_id=a.get("access_key_id"),
        secret_access_key=a.get("secret_access_key"),
        security_group=a.get("security_group"),
        subnets=_subnets_raw,
        key_name=a.get("key_name"),
        ssh_user=a.get("ssh_user", "ubuntu"),
    )
    if a.get("max_runners") is not None:
        v = a["max_runners"]
        assert isinstance(v, int) and v > 0, (
            "config.providers.aws.max_runners: must be an integer > 0"
        )
        _aws_kwargs["max_runners"] = v
    if a.get("end_of_life") is not None:
        v = a["end_of_life"]
        assert isinstance(v, int) and 0 < v < 60, (
            "config.providers.aws.end_of_life: must be an integer > 0 and < 60"
        )
        _aws_kwargs["end_of_life"] = v
    if a.get("recycle") is not None:
        v = a["recycle"]
        assert isinstance(v, bool), (
            "config.providers.aws.recycle: is not a boolean"
        )
        _aws_kwargs["recycle"] = v
    if a.get("recycle_grace_period") is not None:
        v = a["recycle_grace_period"]
        assert isinstance(v, int) and v >= 0, (
            "config.providers.aws.recycle_grace_period: must be an integer >= 0"
        )
        _aws_kwargs["recycle_grace_period"] = v
    _aws_defaults_raw = a.get("defaults")
    if _aws_defaults_raw is not None:
        assert isinstance(
            _aws_defaults_raw, dict
        ), "config.providers.aws.defaults: is not a dictionary"
        base = aws_provider().defaults
        _aws_volume_size = _aws_defaults_raw.get("volume_size", base.volume_size)
        assert isinstance(_aws_volume_size, int) and _aws_volume_size > 0, (
            "config.providers.aws.defaults.volume_size: must be an integer > 0 (in GB)"
        )
        _aws_kwargs["defaults"] = provider_defaults(
            image=_aws_defaults_raw.get("image", base.image),
            server_type=_aws_defaults_raw.get("server_type", base.server_type),
            location=_aws_defaults_raw.get("location", base.location),
            volume_size=_aws_volume_size,
            volume_location=_aws_defaults_raw.get(
                "volume_location", base.volume_location
            ),
            volume_type=_aws_defaults_raw.get("volume_type", base.volume_type),
        )
    return aws_provider(**_aws_kwargs)
