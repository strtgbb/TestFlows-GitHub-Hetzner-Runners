"""Tests that scale_down's recycle path dispatches through the CloudProvider
abstraction (provider-agnostic), rather than calling native cloud SDK methods.

These lock in the Phase 1 refactor (provider-abstracted recycle path) and the
provider-aware SSH-key ownership check: a server is only recycled/deleted after
verifying its stored SSH-key name (read via the provider's own tag) matches one
of this controller's keys for that provider.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from testflows.core import *

from testflows.runners.cloud_provider import ProviderServer
from testflows.runners.scale_down import recycle_server, delete_recyclable_server
from testflows.runners.constants import (
    recycle_timestamp_label,
    recycle_server_name_prefix,
)


def _server(name, server_type="cx22", location="nbg1"):
    return ProviderServer(
        id="id-" + name,
        name=name,
        status="off",
        public_ipv4="1.2.3.4",
        private_ipv4=None,
        labels={},
        server_type=server_type,
        location=location,
        created=datetime.now(timezone.utc),  # fresh -> age minutes ~0
    )


def _provider(stored_ssh_key_name, name="hetzner"):
    """A mock provider whose stored SSH-key name is controllable per test."""
    provider = MagicMock()
    provider.name = name
    provider.get_server_ssh_key_name.return_value = stored_ssh_key_name
    return provider


def _ssh_keys(*names, provider_name="hetzner"):
    keys = []
    for n in names:
        k = MagicMock()
        k.name = n
        keys.append(k)
    return {provider_name: keys}


@TestScenario
def mark_fresh_owned_server_for_recycle_via_provider(self):
    """A fresh, owned server is parked via provider.power_off_server + update_server."""
    provider = _provider(stored_ssh_key_name="mykey")
    server = _server("github-runner-1-0-cx22")
    with When("recycle_server runs on a fresh, owned, non-recycle server"):
        recycle_server(
            reason="powered_off",
            server=server,
            provider=provider,
            ssh_keys=_ssh_keys("mykey"),
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
def delete_when_not_owned_via_provider(self):
    """A server whose stored SSH key isn't ours is deleted through the provider."""
    with When("the server has no stored SSH-key name"):
        provider = _provider(stored_ssh_key_name=None)
        server = _server("github-runner-1-0-cx22")
        recycle_server(
            reason="zombie",
            server=server,
            provider=provider,
            ssh_keys=_ssh_keys("mykey"),
            end_of_life=60,
            recycle_grace_period=0,
        )
    with Then("it deletes via the provider and does not power off"):
        provider.delete_server.assert_called_once_with(server)
        provider.power_off_server.assert_not_called()

    with When("the server's stored SSH key belongs to a different controller"):
        provider2 = _provider(stored_ssh_key_name="someone-elses-key")
        server2 = _server("github-runner-2-0-cx22")
        recycle_server(
            reason="zombie",
            server=server2,
            provider=provider2,
            ssh_keys=_ssh_keys("mykey"),
            end_of_life=60,
            recycle_grace_period=0,
        )
    with Then("it is also deleted as not owned"):
        provider2.delete_server.assert_called_once_with(server2)
        provider2.power_off_server.assert_not_called()


@TestScenario
def set_recycle_timestamp_via_provider(self):
    """A recycle-prefixed owned server past end-of-life with no timestamp gets tagged."""
    provider = _provider(stored_ssh_key_name="mykey")
    name = f"{recycle_server_name_prefix}abc"
    server = _server(name)
    with When("recycle_server runs on a recycle server missing its timestamp"):
        recycle_server(
            reason="unused_recyclable",
            server=server,
            provider=provider,
            ssh_keys=_ssh_keys("mykey"),
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
    """scale_down recycle path is provider-agnostic with provider-aware key checks."""
    for scenario in loads(current_module(), Scenario):
        scenario()
