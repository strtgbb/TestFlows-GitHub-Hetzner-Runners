import re
import hashlib

from .constants import runner_name_prefix

_RUNNER_TAG_MAX = 63
_RUNNER_TAG_DISALLOWED = re.compile(r"[^a-z0-9._-]+")


def derive_runner_tag(github_repository: str, with_label) -> str:
    """Controller-id value for the runner discovery label.

    A sanitized readable part (repo + sorted ``with_label``, lowercased, hcloud
    charset ``[a-z0-9._-]``, alphanumeric ends) plus an always-appended 8-hex
    hash of the unsanitized identity, so distinct inputs never collide. Capped
    at 63 chars.
    """
    labels = sorted(with_label or [])
    repo = github_repository or ""
    # Hash the raw NUL-joined identity (NUL can't occur in the inputs).
    digest = hashlib.sha256(
        "\x00".join([repo, *labels]).encode("utf-8")
    ).hexdigest()[:8]
    base = "-".join([repo, *labels]).lower()
    readable = _RUNNER_TAG_DISALLOWED.sub("-", base).strip("-._")
    readable = readable[: _RUNNER_TAG_MAX - 9].strip("-._")
    return f"{readable}-{digest}" if readable else digest


def format_runner_name(run_id, job_id, server_type: str) -> str:
    """Build the canonical runner/server name from its identity parts.

    Single source of truth for the format ``{prefix}{run_id}-{job_id}-{type}``;
    get_runner_server_type is its inverse. server_type must be dash-free
    (canonical dot-form) so the name round-trips.
    """
    return f"{runner_name_prefix}{run_id}-{job_id}-{server_type}"


def parse_runner_name(runner_name: str) -> tuple[str, str, str] | None:
    """Parse a canonical runner name into ``(run_id, job_id, server_type)``.

    Inverse of format_runner_name for names shaped
    ``{prefix}{run_id}-{job_id}-{server_type}``. The server type may contain
    dots (e.g. 'c8g.2xlarge') but never dashes, so everything after the second
    dash past the prefix is the type. Returns None when the name is not in this
    format (e.g. standby names, which have no run/job id).
    """
    if not runner_name or not runner_name.startswith(runner_name_prefix):
        return None
    parts = runner_name[len(runner_name_prefix):].split("-", 2)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def get_runner_server_type(runner_name: str) -> str | None:
    """Return the server type embedded in a runner name, or None."""
    parsed = parse_runner_name(runner_name)
    return parsed[2] if parsed is not None else None
