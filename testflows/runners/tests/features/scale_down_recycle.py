"""Tests that scale_down's recycle path dispatches through the CloudProvider
abstraction (provider-agnostic), rather than calling native cloud SDK methods.

These lock in the Phase 1 refactor: recycle_server and delete_recyclable_server
operate on ProviderServer objects and route mark/delete/power-off/tag operations
through the resolved provider.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from testflows.core import *

from testflows.runners.cloud_provider import ProviderServer
from testflows.runners.scale_down import recycle_server, delete_recyclable_server
from testflows.runners.constants import (
    server_ssh_key_label,
    recycle_timestamp_label,
    recycle_server_name_prefix,
)


def _server(name, labels=None, server_type="cx22", location="nbg1"):
    return ProviderServer(
        id="id-" + name,
        name=name,
        status="off",
        public_ipv4="1.2.3.4",
        private_ipv4=None,
        labels=labels if labels is not None else {},
        server_type=server_type,
        location=location,
        created=datetime.now(timezone.utc),  # fresh -> age minutes ~0
    )


def _ssh_key(name="mykey"):
    key = MagicMock()
    key.name = name
    return key


@TestScenario
def mark_fresh_server_for_recycle_via_provider(self):
    """A fresh owned server is parked via provider.power_off_server + update_server."""
    provider = MagicMock()
    server = _server("github-runner-1-0-cx22", labels={server_ssh_key_label: "mykey"})
    with When("recycle_server runs on a fresh, owned, non-recycle server"):
        recycle_server(
            reason="powered_off",
            server=server,
            provider=provider,
            ssh_key=_ssh_key("mykey"),
            end_of_life=60,
            recycle_grace_period=0,
        )
    with Then("it powers off and renames via the provider, not native calls"):
        provider.power_off_server.assert_called_once_with(server)
        assert provider.update_server.call_count == 1, provider.update_server.call_count
        _, kwargs = provider.update_server.call_args
        assert kwargs["name"].startswith(recycle_server_name_prefix), kwargs["name"]
        assert recycle_timestamp_label in kwargs["labels"]
        provider.delete_server.assert_not_called()


@TestScenario
def delete_when_no_ssh_key_label_via_provider(self):
    """A server missing the SSH-key label is deleted through the provider."""
    provider = MagicMock()
    server = _server("github-runner-1-0-cx22", labels={})
    with When("recycle_server runs on a server with no ssh-key label"):
        recycle_server(
            reason="zombie",
            server=server,
            provider=provider,
            ssh_key=_ssh_key("mykey"),
            end_of_life=60,
            recycle_grace_period=0,
        )
    with Then("it deletes via the provider and does not power off"):
        provider.delete_server.assert_called_once_with(server)
        provider.power_off_server.assert_not_called()


@TestScenario
def set_recycle_timestamp_via_provider(self):
    """A recycle-prefixed server past end-of-life with no timestamp gets tagged."""
    provider = MagicMock()
    name = f"{recycle_server_name_prefix}abc"
    server = _server(name, labels={server_ssh_key_label: "mykey"})
    with When("recycle_server runs on a recycle server missing its timestamp"):
        recycle_server(
            reason="unused_recyclable",
            server=server,
            provider=provider,
            ssh_key=_ssh_key("mykey"),
            end_of_life=0,  # already past end-of-life
            recycle_grace_period=60,
        )
    with Then("it sets the recycle timestamp tag via the provider (no delete)"):
        provider.set_server_tags.assert_called_once()
        args, _ = provider.set_server_tags.call_args
        assert args[0] is server
        assert recycle_timestamp_label in args[1]
        provider.delete_server.assert_not_called()


@TestScenario
def delete_recyclable_resolves_provider_per_server(self):
    """delete_recyclable_server deletes the picked server via its own provider."""
    provider = MagicMock()
    s1 = _server(f"{recycle_server_name_prefix}one")
    s2 = _server(f"{recycle_server_name_prefix}two")
    server_providers = {s1.name: provider, s2.name: provider}
    with When("delete_recyclable_server is called with ProviderServers"):
        deleted = delete_recyclable_server(
            server_name="github-runner-9-0-cx22",
            recyclable_servers=[s1, s2],
            server_providers=server_providers,
            server_prices=None,  # random pick
            recycle_grace_period=0,
        )
    with Then("exactly one server is deleted through the provider"):
        assert provider.delete_server.call_count == 1, provider.delete_server.call_count
        assert deleted in (s1.name, s2.name), deleted


@TestFeature
@Name("scale_down recycle")
def feature(self):
    """scale_down recycle path is provider-agnostic."""
    for scenario in loads(current_module(), Scenario):
        scenario()
