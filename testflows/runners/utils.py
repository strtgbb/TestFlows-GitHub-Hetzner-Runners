import re
import hashlib

from .constants import runner_name_prefix

_RUNNER_TAG_MAX = 63
_RUNNER_TAG_DISALLOWED = re.compile(r"[^a-z0-9._-]+")


def derive_runner_tag(github_repository: str, with_label) -> str:
    """Derive a controller identity for the runner discovery-label value.

    Stable and provider-portable: the repo plus the sorted ``with_label`` set,
    lowercased and sanitized to the strictest label charset (hcloud label
    value: ``[a-z0-9._-]``, <= 63 chars). Two controllers with a different repo
    or a different ``with_label`` set get different ids, so they never manage
    each other's servers. When the sanitized id would exceed the length limit
    it is truncated with a short stable hash suffix to keep it unique.
    """
    parts = [github_repository or ""] + sorted(with_label or [])
    base = "-".join(parts).lower()
    tag = _RUNNER_TAG_DISALLOWED.sub("-", base).strip("-")
    if len(tag) > _RUNNER_TAG_MAX:
        digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()[:8]
        tag = tag[: _RUNNER_TAG_MAX - 9].strip("-") + "-" + digest
    return tag


def format_runner_name(run_id, job_id, server_type: str) -> str:
    """Build the canonical runner/server name from its identity parts.

    Single source of truth for the format ``{prefix}{run_id}-{job_id}-{type}``;
    get_runner_server_type is its inverse. server_type must be dash-free
    (canonical dot-form) so the name round-trips.
    """
    return f"{runner_name_prefix}{run_id}-{job_id}-{server_type}"


def get_runner_server_type(runner_name: str) -> str | None:
    """Return the server type embedded in a runner name, or None.

    Runner names follow the pattern:
      github-runner-{run_id}-{job_id}-{server_type}

    The server type may contain dots (e.g. 'c8g.2xlarge') so the split is
    capped at 4 splits to capture everything after the fourth dash as the type.
    """
    if runner_name and runner_name.startswith(runner_name_prefix):
        parts = runner_name.split("-", 4)
        if len(parts) == 5:
            return parts[4]
    return None
