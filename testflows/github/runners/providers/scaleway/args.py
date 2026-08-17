"""Scaleway provider argument validators.

The ``server_type`` validator is the CLI-side guard for the ``-`` problem: a
user copy-pasting Scaleway's docs would write ``DEV1-S``, but the runner label
grammar reserves ``-`` as a separator, so server types must be given in the
canonical dot-form (``dev1.s``).  We reject the dash-form here with a message
that points at the dot-form, and accept the dot-form for the default type.

(Job labels like ``type-dev1.s`` cannot be validated here — they arrive at
runtime from GitHub — so the README documents the same convention.)
"""

import re
import uuid
from argparse import ArgumentTypeError


# Canonical (dot-form) Scaleway type: alphanumeric segments joined by dots,
# e.g. dev1.s, gp1.xs, pop2.2c.8g.
_CANONICAL_TYPE_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9]+)+$")

# Scaleway Availability Zone, e.g. fr-par-1, nl-ams-2, pl-waw-3.
_ZONE_RE = re.compile(r"^[a-z]{2}-[a-z]{3}-\d$")


def server_type(v):
    """Scaleway commercial instance type in canonical dot-form. Example: dev1.s

    Scaleway's own type names use ``-`` (``DEV1-S``); that collides with the
    runner label grammar, so we require the dot-form here.
    """
    value = v.strip().lower()
    if "-" in value:
        canonical = value.replace("-", ".")
        raise ArgumentTypeError(
            f"invalid Scaleway server type '{v}': use the dot-form "
            f"'{canonical}' instead of the dash-form (the runner label grammar "
            f"reserves '-' as a separator)"
        )
    if not _CANONICAL_TYPE_RE.match(value):
        raise ArgumentTypeError(
            f"invalid Scaleway server type '{v}': expected a dot-form name like "
            f"'dev1.s', 'gp1.xs' or 'pop2.2c.8g'"
        )
    return value


def location_type(v):
    """Scaleway Availability Zone argument. Example: fr-par-1"""
    value = v.strip().lower()
    if not _ZONE_RE.match(value):
        raise ArgumentTypeError(
            f"invalid Scaleway zone '{v}': must be in format like 'fr-par-1', "
            f"'nl-ams-2' or 'pl-waw-3'"
        )
    return value


def image_type(v):
    """Scaleway image argument: an image UUID or a marketplace label.

    Examples: ``ubuntu_jammy`` (marketplace label) or a UUID.
    """
    value = v.strip()
    try:
        uuid.UUID(value)
        return value
    except (ValueError, AttributeError):
        pass
    # Marketplace image labels are lowercase alphanumerics with underscores.
    if re.match(r"^[a-z0-9_]+$", value):
        return value
    raise ArgumentTypeError(
        f"invalid Scaleway image '{v}': expected an image UUID or a marketplace "
        f"label such as 'ubuntu_jammy'"
    )


def add_arguments(parser):
    """Add Scaleway-specific CLI arguments to parser."""
    group = parser.add_argument_group("Scaleway options")

    group.add_argument(
        "--scaleway-access-key",
        metavar="key",
        type=str,
        help="Scaleway API access key, default: project config or $SCW_ACCESS_KEY",
    )
    group.add_argument(
        "--scaleway-secret-key",
        metavar="secret",
        type=str,
        help="Scaleway API secret key, default: project config or $SCW_SECRET_KEY",
    )
    group.add_argument(
        "--scaleway-project-id",
        metavar="uuid",
        type=str,
        help="Scaleway project ID, default: project config or $SCW_DEFAULT_PROJECT_ID",
    )
    group.add_argument(
        "--scaleway-organization-id",
        metavar="uuid",
        type=str,
        help="Scaleway organization ID, default: project config or $SCW_DEFAULT_ORGANIZATION_ID",
    )
    group.add_argument(
        "--scaleway-default-image",
        metavar="image",
        type=image_type,
        help="Default Scaleway image UUID or marketplace label (ubuntu_jammy)",
    )
    group.add_argument(
        "--scaleway-default-server-type",
        metavar="type",
        type=server_type,
        help="Default Scaleway instance type in dot-form (dev1.s, gp1.xs)",
    )
    group.add_argument(
        "--scaleway-default-location",
        metavar="zone",
        type=location_type,
        help="Default Scaleway zone (fr-par-1)",
    )

    group.add_argument(
        "--scaleway-default-disk-size",
        metavar="GB",
        type=int,
        help="Default Scaleway SBS boot disk size in GB (20)",
    )
