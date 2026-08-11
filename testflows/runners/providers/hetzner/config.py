"""Hetzner Cloud provider configuration."""

import base64
import hashlib

from hcloud.images.domain import Image
from hcloud.server_types.domain import ServerType
from hcloud.locations.domain import Location
from hcloud.ssh_keys.domain import SSHKey

from ...hclient import HClient as Client
from ...actions import Action
from ... import errors
from ...config.config import hetzner_provider, provider_defaults


def is_enabled(provider_config):
    """Check if Hetzner provider is enabled (has required credentials)."""
    return provider_config and provider_config.token


def get_cli_fields():
    """Get list of all CLI field names for Hetzner provider."""
    return [
        "token",
        "recycle_with_rebuild",
        "default_image",
        "default_server_type",
        "default_location",
        "default_volume_size",
        "default_volume_location",
    ]


def has_cli_args(args):
    """Check if any Hetzner CLI arguments are provided."""
    return any(
        getattr(args, f"hetzner_{field}", None) is not None
        for field in get_cli_fields()
    )


def update_from_args(provider_config, args):
    """Update Hetzner provider configuration from CLI arguments."""
    if not provider_config:
        return

    # Update credentials
    if getattr(args, "hetzner_token", None) is not None:
        provider_config.token = args.hetzner_token
    if getattr(args, "hetzner_recycle_with_rebuild", None) is not None:
        provider_config.recycle_with_rebuild = args.hetzner_recycle_with_rebuild

    # Update defaults
    if getattr(args, "hetzner_default_image", None) is not None:
        provider_config.defaults.image = args.hetzner_default_image
    if getattr(args, "hetzner_default_server_type", None) is not None:
        provider_config.defaults.server_type = args.hetzner_default_server_type
    if getattr(args, "hetzner_default_location", None) is not None:
        provider_config.defaults.location = args.hetzner_default_location
    if getattr(args, "hetzner_default_volume_size", None) is not None:
        provider_config.defaults.volume_size = args.hetzner_default_volume_size
    if getattr(args, "hetzner_default_volume_location", None) is not None:
        provider_config.defaults.volume_location = args.hetzner_default_volume_location


# Hetzner-specific validation functions


def parse_config_section(section: dict) -> "hetzner_provider":
    """Validate and coerce a ``providers.hetzner`` config section into a
    ``hetzner_provider`` dataclass. Moved from config/parse.py verbatim.
    """
    h = section
    assert isinstance(h, dict), "config.providers.hetzner: is not a dictionary"
    if h.get("token") is not None:
        assert isinstance(
            h["token"], str
        ), "config.providers.hetzner.token: is not a string"
    _hetzner_kwargs = {"token": h.get("token")}
    if h.get("recycle_with_rebuild") is not None:
        v = h["recycle_with_rebuild"]
        assert isinstance(v, bool), (
            "config.providers.hetzner.recycle_with_rebuild: "
            "is not a boolean"
        )
        _hetzner_kwargs["recycle_with_rebuild"] = v
    if h.get("max_runners") is not None:
        v = h["max_runners"]
        assert isinstance(v, int) and v > 0, (
            "config.providers.hetzner.max_runners: must be an integer > 0"
        )
        _hetzner_kwargs["max_runners"] = v
    if h.get("end_of_life") is not None:
        v = h["end_of_life"]
        assert isinstance(v, int) and 0 < v < 60, (
            "config.providers.hetzner.end_of_life: must be an integer > 0 and < 60"
        )
        _hetzner_kwargs["end_of_life"] = v
    if h.get("recycle") is not None:
        v = h["recycle"]
        assert isinstance(v, bool), (
            "config.providers.hetzner.recycle: is not a boolean"
        )
        _hetzner_kwargs["recycle"] = v
    if h.get("recycle_grace_period") is not None:
        v = h["recycle_grace_period"]
        assert isinstance(v, int) and v >= 0, (
            "config.providers.hetzner.recycle_grace_period: must be an integer >= 0"
        )
        _hetzner_kwargs["recycle_grace_period"] = v
    _hetzner_defaults_raw = h.get("defaults")
    if _hetzner_defaults_raw is not None:
        assert isinstance(
            _hetzner_defaults_raw, dict
        ), "config.providers.hetzner.defaults: is not a dictionary"
        base = hetzner_provider().defaults
        _hetzner_volume_size = _hetzner_defaults_raw.get(
            "volume_size", base.volume_size
        )
        assert (
            isinstance(_hetzner_volume_size, int) and _hetzner_volume_size > 0
        ), (
            "config.providers.hetzner.defaults.volume_size: must be an integer > 0 (in GB)"
        )
        _hetzner_kwargs["defaults"] = provider_defaults(
            image=_hetzner_defaults_raw.get("image", base.image),
            server_type=_hetzner_defaults_raw.get(
                "server_type", base.server_type
            ),
            location=_hetzner_defaults_raw.get("location", base.location),
            volume_size=_hetzner_volume_size,
            volume_location=_hetzner_defaults_raw.get(
                "volume_location", base.volume_location
            ),
        )
    return hetzner_provider(**_hetzner_kwargs)


def check_ssh_key(client: Client, ssh_key: str, is_file=True):
    """Check that ssh key exists if not create it."""

    def fingerprint(ssh_key):
        """Calculate fingerprint of a public SSH key."""
        encoded_key = base64.b64decode(ssh_key.strip().split()[1].encode("utf-8"))
        md5_digest = hashlib.md5(encoded_key).hexdigest()

        return ":".join(a + b for a, b in zip(md5_digest[::2], md5_digest[1::2]))

    if is_file:
        with open(ssh_key, "r", encoding="utf-8") as ssh_key_file:
            public_key = ssh_key_file.read()
    else:
        public_key = ssh_key

    name = hashlib.md5(public_key.encode("utf-8")).hexdigest()
    ssh_key: SSHKey = SSHKey(
        name=name, public_key=public_key, fingerprint=fingerprint(public_key)
    )

    existing_ssh_key = client.ssh_keys.get_by_fingerprint(
        fingerprint=ssh_key.fingerprint
    )

    if not existing_ssh_key:
        with Action(
            f"Creating SSH key {ssh_key.name} with fingerprint {ssh_key.fingerprint}",
            stacklevel=3,
        ):
            ssh_key = client.ssh_keys.create(
                name=ssh_key.name, public_key=ssh_key.public_key
            )
    else:
        ssh_key = existing_ssh_key

    return ssh_key


def check_image(client: Client, image: Image):
    """Check if image exists.
    If image type is not 'system' then use image description to find it.
    """

    if image.type in ("system", "app"):
        _image = client.images.get_by_name_and_architecture(
            name=image.name, architecture=image.architecture
        )
        if not _image:
            raise errors.ImageError(
                f"image type:'{image.type}' name:'{image.name}' architecture:'{image.architecture}' not found"
            )
        return _image
    else:
        # backup or snapshot
        try:
            return [
                i
                for i in client.images.get_all(
                    type=image.type, architecture=image.architecture
                )
                if i.description == image.description
            ][0]
        except IndexError:
            raise errors.ImageError(
                f"image type:'{image.type}' name:'{image.description}' architecture:'{image.architecture}' not found"
            )


def check_location(client: Client, location: Location, required=False):
    """Check if location exists."""
    if location is None:
        if required:
            raise errors.LocationError(f"location is not defined")
        return None
    _location = client.locations.get_by_name(location.name)
    if not _location:
        raise errors.LocationError(f"location '{location.name}' not found")
    return _location


def check_server_type(client: Client, server_type: ServerType):
    """Check if server type exists."""
    _type: ServerType = client.server_types.get_by_name(server_type.name)
    if not _type:
        raise errors.ServerTypeError(f"server type '{server_type.name}' not found")
    return _type


def check_prices(client: Client):
    """Check server prices."""
    server_types: list[ServerType] = client.server_types.get_all()
    return {
        t.name.lower(): {
            price["location"]: float(price["price_hourly"]["gross"])
            for price in t.prices
        }
        for t in server_types
    }
