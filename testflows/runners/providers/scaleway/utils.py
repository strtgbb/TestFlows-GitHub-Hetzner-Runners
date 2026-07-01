"""Scaleway API adapter utilities.

This module holds the pieces that are pure logic and therefore unit-testable
without the Scaleway SDK or live credentials:

* the canonical <-> native instance-type translation (the ``-`` problem), and
* the Scaleway ``list[str]`` tag <-> ``dict`` label conversion, and
* the Scaleway ``Server`` -> :class:`ProviderServer` mapping.

Scaleway instance types contain ``-`` (e.g. ``DEV1-S``, ``POP2-2C-8G``), but
the runner label grammar and runner-name encoding both reserve ``-`` as a
separator.  We therefore keep a **canonical** dot-form (``dev1.s``) everywhere
in the orchestrator (labels, runner names, ``ProviderServer.server_type``,
price-map keys) and only translate to the **native** dash-form (``DEV1-S``) at
the Scaleway API boundary inside ``provider.py``.

Because Scaleway type names never contain ``.`` and the canonical form never
contains ``-``, the ``.`` <-> ``-`` swap is a clean bijection that also handles
multi-dash types (``POP2-2C-8G`` <-> ``pop2.2c.8g``).
"""

from datetime import datetime, timezone

from ...cloud_provider import CloudProvider, ProviderServer


# Scaleway tag conventions. Scaleway tags are a flat ``list[str]``; we encode
# ``key=value`` pairs as individual tag strings to present a dict to the rest
# of the system (mirrors the AWS tag-key conventions).
_RUNNER_TAG = "github-runner"
_RUNNER_LABEL_TAG_PREFIX = "github-runner-label"
_SSH_KEY_TAG = "github-runner-ssh-key"

# Scaleway ServerState values -> abstract CloudProvider status constants.
# See scaleway.instance.v1.ServerState: running / stopped / stopped_in_place /
# starting / stopping / locked.
_STATE_MAP = {
    "running": CloudProvider.STATUS_RUNNING,
    "stopped": CloudProvider.STATUS_OFF,
    "stopped_in_place": CloudProvider.STATUS_OFF,
    "starting": CloudProvider.STATUS_STARTING,
    "stopping": CloudProvider.STATUS_STOPPING,
    "locked": CloudProvider.STATUS_UNKNOWN,
}

# Server states considered "active" (still allocated / billable as compute or
# pending), included in server listings.
_ACTIVE_STATES = ["running", "starting", "stopping", "stopped", "stopped_in_place"]


def canonical_type(native: str) -> str:
    """Translate a native Scaleway type to the canonical orchestrator form.

    ``DEV1-S`` -> ``dev1.s``; ``POP2-2C-8G`` -> ``pop2.2c.8g``.
    """
    return native.replace("-", ".").lower() if native else native


def native_type(canonical: str) -> str:
    """Translate a canonical orchestrator type back to the native Scaleway form.

    ``dev1.s`` -> ``DEV1-S``; ``pop2.2c.8g`` -> ``POP2-2C-8G``.
    """
    return canonical.replace(".", "-").upper() if canonical else canonical


def tags_to_dict(tags) -> dict:
    """Convert a Scaleway ``list[str]`` of ``key=value`` tags to a dict.

    Bare tags without ``=`` are stored with an empty-string value so they round
    trip back through :func:`dict_to_tags`.
    """
    result = {}
    for tag in tags or []:
        if "=" in tag:
            key, value = tag.split("=", 1)
            result[key] = value
        else:
            result[tag] = ""
    return result


def dict_to_tags(labels: dict) -> list:
    """Convert a label dict to Scaleway's ``list[str]`` tag format.

    Empty-string values are emitted as bare tags (no trailing ``=``).
    """
    tags = []
    for key, value in (labels or {}).items():
        tags.append(f"{key}={value}" if value != "" else key)
    return tags


def _server_to_provider(server, ssh_user: str = "root") -> ProviderServer:
    """Convert a Scaleway SDK ``Server`` object to a :class:`ProviderServer`.

    ``server.commercial_type`` is the native dash-form (``DEV1-S``); it is
    translated to the canonical dot-form so the rest of the orchestrator only
    ever sees one representation.
    """
    public_ipv4 = None
    public_ipv6 = None
    # Scaleway exposes a primary ``public_ip`` and a ``public_ips`` list; each
    # entry carries ``.address`` and ``.family`` ("inet" / "inet6").
    for ip in getattr(server, "public_ips", None) or []:
        family = getattr(ip, "family", None)
        address = getattr(ip, "address", None)
        if family == "inet6" and public_ipv6 is None:
            public_ipv6 = address
        elif public_ipv4 is None:
            public_ipv4 = address
    if public_ipv4 is None:
        primary = getattr(server, "public_ip", None)
        if primary is not None:
            public_ipv4 = getattr(primary, "address", None)

    private_ipv4 = getattr(server, "private_ip", None)

    state = (getattr(server, "state", "") or "").lower()
    zone = str(getattr(server, "zone", "") or "")

    return ProviderServer(
        id=server.id,
        name=server.name,
        status=_STATE_MAP.get(state, CloudProvider.STATUS_UNKNOWN),
        public_ipv4=public_ipv4,
        private_ipv4=private_ipv4,
        public_ipv6=public_ipv6,
        labels=tags_to_dict(getattr(server, "tags", None)),
        server_type=canonical_type(getattr(server, "commercial_type", "") or ""),
        location=zone,
        created=getattr(server, "creation_date", None) or datetime.now(timezone.utc),
        volumes=[],
        ssh_user=ssh_user,
        _native=server,
    )
