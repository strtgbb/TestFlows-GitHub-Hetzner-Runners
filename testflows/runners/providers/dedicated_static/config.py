"""Dedicated-static provider configuration."""

import re

from ...config_schema import (
    dedicated_static_provider,
    dedicated_static_group,
    dedicated_static_ssh,
)
from ...argtypes import path_type as path

DEDICATED_STATIC_GROUP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def parse_config_section(
    section: dict, meta_label: dict = None, label_prefix: str = ""
) -> "dedicated_static_provider":
    """Validate and coerce a ``providers.dedicated_static`` config section into a
    ``dedicated_static_provider`` dataclass.

    ``meta_label`` and ``label_prefix`` come from the top-level document (not the
    section itself) because group label validation needs them to resolve the
    same prefixed/meta labels that get_server_types resolves at runtime.
    """
    d = section
    assert isinstance(d, dict), "config.providers.dedicated_static: is not a dictionary"

    claim_ttl_minutes = d.get("claim_ttl_minutes", 360)
    assert (
        isinstance(claim_ttl_minutes, int) and claim_ttl_minutes > 0
    ), "config.providers.dedicated_static.claim_ttl_minutes: must be an integer > 0"

    ssh_defaults_raw = d.get("ssh_defaults") or {}
    assert isinstance(
        ssh_defaults_raw, dict
    ), "config.providers.dedicated_static.ssh_defaults: is not a dictionary"

    ssh_defaults = dedicated_static_ssh()
    if ssh_defaults_raw.get("user") is not None:
        assert isinstance(
            ssh_defaults_raw["user"], str
        ), "config.providers.dedicated_static.ssh_defaults.user: is not a string"
        assert (
            ssh_defaults_raw["user"].strip()
        ), "config.providers.dedicated_static.ssh_defaults.user: cannot be empty"
        ssh_defaults.user = ssh_defaults_raw["user"].strip()
    if ssh_defaults_raw.get("port") is not None:
        assert (
            isinstance(ssh_defaults_raw["port"], int)
            and 1 <= ssh_defaults_raw["port"] <= 65535
        ), "config.providers.dedicated_static.ssh_defaults.port: must be an integer between 1 and 65535"
        ssh_defaults.port = ssh_defaults_raw["port"]
    if ssh_defaults_raw.get("key") is not None:
        assert isinstance(
            ssh_defaults_raw["key"], str
        ), "config.providers.dedicated_static.ssh_defaults.key: is not a string"
        assert (
            ssh_defaults_raw["key"].strip()
        ), "config.providers.dedicated_static.ssh_defaults.key: cannot be empty"
        ssh_defaults.key = path(ssh_defaults_raw["key"].strip(), check_exists=False)

    groups_raw = d.get("groups")
    assert isinstance(
        groups_raw, dict
    ), "config.providers.dedicated_static.groups: is not a dictionary"
    assert groups_raw, "config.providers.dedicated_static.groups: cannot be empty"

    groups: dict[str, dedicated_static_group] = {}
    meta = meta_label or {}

    # Type labels carry the configured label_prefix at runtime
    # (get_server_types prepends "<label_prefix>-" before "type-"), so the
    # validation must look for the same prefixed form, not a bare "type-".
    # label_prefix conventionally ends with "-" (like server_name_prefix);
    # tolerate either form, matching get_server_types.
    type_prefix = (label_prefix or "").strip().lower()
    if type_prefix and not type_prefix.endswith("-"):
        type_prefix += "-"
    type_prefix += "type-"

    for group_name, group in groups_raw.items():
        assert isinstance(
            group_name, str
        ), "config.providers.dedicated_static.groups: group name is not a string"
        assert (
            group_name.strip()
        ), "config.providers.dedicated_static.groups: group name cannot be empty"
        group_name = group_name.strip().lower()
        assert DEDICATED_STATIC_GROUP_NAME_RE.match(group_name), (
            "config.providers.dedicated_static.groups: invalid group name "
            f"'{group_name}' (must match ^[a-z0-9][a-z0-9-]*$)"
        )
        assert isinstance(
            group, dict
        ), f"config.providers.dedicated_static.groups.{group_name}: is not a dictionary"

        labels = group.get("labels")
        assert isinstance(
            labels, list
        ), f"config.providers.dedicated_static.groups.{group_name}.labels: is not a list"
        normalized_labels = []
        for i, label in enumerate(labels):
            assert isinstance(
                label, str
            ), f"config.providers.dedicated_static.groups.{group_name}.labels[{i}]: is not a string"
            label = label.lower().strip()
            assert (
                label
            ), f"config.providers.dedicated_static.groups.{group_name}.labels[{i}]: cannot be empty"
            normalized_labels.append(label)
            if label in meta:
                normalized_labels.extend(
                    [meta_label_.lower().strip() for meta_label_ in meta[label]]
                )
        normalized_labels = list(dict.fromkeys(normalized_labels))
        assert any(
            l.startswith(type_prefix) for l in normalized_labels
        ), f"config.providers.dedicated_static.groups.{group_name}.labels: must include at least one '{type_prefix}*' label (or a meta label that expands to one)"
        for label in normalized_labels:
            if label.startswith(type_prefix):
                type_name = label.split(type_prefix, 1)[1]
                assert "-" not in type_name, (
                    f"config.providers.dedicated_static.groups.{group_name}.labels: "
                    f"invalid type label '{label}' (type names with '-' are not supported)"
                )

        hosts = group.get("hosts")
        assert isinstance(
            hosts, list
        ), f"config.providers.dedicated_static.groups.{group_name}.hosts: is not a list"
        assert (
            hosts
        ), f"config.providers.dedicated_static.groups.{group_name}.hosts: cannot be empty"
        normalized_hosts = []
        for i, host in enumerate(hosts):
            assert isinstance(
                host, str
            ), f"config.providers.dedicated_static.groups.{group_name}.hosts[{i}]: is not a string"
            host = host.strip()
            assert (
                host
            ), f"config.providers.dedicated_static.groups.{group_name}.hosts[{i}]: cannot be empty"
            normalized_hosts.append(host)

        group_ssh = None
        if group.get("ssh") is not None:
            raw_ssh = group["ssh"]
            assert isinstance(
                raw_ssh, dict
            ), f"config.providers.dedicated_static.groups.{group_name}.ssh: is not a dictionary"
            group_ssh = dedicated_static_ssh(
                user=ssh_defaults.user,
                port=ssh_defaults.port,
                key=ssh_defaults.key,
            )
            if raw_ssh.get("user") is not None:
                assert isinstance(
                    raw_ssh["user"], str
                ), f"config.providers.dedicated_static.groups.{group_name}.ssh.user: is not a string"
                assert (
                    raw_ssh["user"].strip()
                ), f"config.providers.dedicated_static.groups.{group_name}.ssh.user: cannot be empty"
                group_ssh.user = raw_ssh["user"].strip()
            if raw_ssh.get("port") is not None:
                assert isinstance(raw_ssh["port"], int) and 1 <= raw_ssh["port"] <= 65535, (
                    f"config.providers.dedicated_static.groups.{group_name}.ssh.port: "
                    "must be an integer between 1 and 65535"
                )
                group_ssh.port = raw_ssh["port"]
            if raw_ssh.get("key") is not None:
                assert isinstance(
                    raw_ssh["key"], str
                ), f"config.providers.dedicated_static.groups.{group_name}.ssh.key: is not a string"
                assert (
                    raw_ssh["key"].strip()
                ), f"config.providers.dedicated_static.groups.{group_name}.ssh.key: cannot be empty"
                group_ssh.key = path(raw_ssh["key"].strip(), check_exists=False)

        groups[group_name] = dedicated_static_group(
            labels=normalized_labels,
            hosts=normalized_hosts,
            ssh=group_ssh,
        )

    return dedicated_static_provider(
        ssh_defaults=ssh_defaults,
        claim_ttl_minutes=claim_ttl_minutes,
        groups=groups,
    )
