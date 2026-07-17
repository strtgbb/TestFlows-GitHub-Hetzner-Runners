"""Transition tests for provider-owned recycling."""

import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from testflows.core import *

from testflows.runners.cloud_provider import (
    CloudProvider,
    ProviderServer,
    RecycleClaim,
    RecycleRequest,
)
from testflows.runners.constants import (
    recycle_image_label,
    recycle_server_name_prefix,
    recycle_timestamp_label,
)
from testflows.runners.recycling import (
    activate_recycled_server,
    retire_to_recycle_pool,
)
from testflows.runners.providers.hetzner.provider import HetznerCloudProvider
from testflows.runners.providers.dedicated_static.provider import (
    DedicatedStaticCloudProvider,
)


def _server(name="github-runner-active", status=CloudProvider.STATUS_RUNNING):
    return ProviderServer(
        id="server-1",
        name=name,
        status=status,
        public_ipv4="192.0.2.1",
        private_ipv4=None,
        public_ipv6=None,
        labels={"ssh-key": "ours", recycle_image_label: "image-a"},
        server_type="type-a",
        location="loc-a",
        created=datetime.now(timezone.utc),
    )


def _provider(server, owned=True):
    provider = MagicMock()
    provider.name = "provider"
    provider.get_server.return_value = server
    provider.has_matching_ssh_key.return_value = owned
    provider.get_server_tag.side_effect = lambda current, key: current.labels.get(key)
    provider.validate_labels.return_value = (True, "")
    provider.is_recycle_claimed.return_value = False
    provider.labels_for_recycled_server.side_effect = (
        lambda current, labels: {
            **{
                key: value
                for key, value in current.labels.items()
                if not key.startswith("runner-label-")
            },
            **labels,
        }
    )
    provider._recycle_claim_state.return_value = (threading.Lock(), set())
    return provider


def _claim(server):
    return RecycleClaim(
        server=server,
        request=RecycleRequest(
            name="github-runner-new",
            server_type=server.server_type,
            location=server.location,
            image="image-a",
            labels={"runner": "active", recycle_image_label: "image-a"},
            ssh_key_names=frozenset({"ours"}),
            enable_ipv4=True,
            enable_ipv6=False,
        ),
    )


@TestScenario
def unmanaged_server_is_not_mutated(self):
    server = _server()
    provider = _provider(server, owned=False)
    result = retire_to_recycle_pool(
        provider,
        server,
        recycle_enabled=True,
        ssh_key_names={"ours"},
        end_of_life=50,
        recycle_grace_period=0,
    )
    assert result.action == "unmanaged"
    provider.delete_server.assert_not_called()
    provider.power_off_server.assert_not_called()


@TestScenario
def deletion_tombstone_blocks_retirement_mutations(self):
    server = _server(f"{recycle_server_name_prefix}one", CloudProvider.STATUS_RUNNING)
    provider = _provider(server)
    provider.is_recycle_claimed.return_value = True
    result = retire_to_recycle_pool(
        provider,
        server,
        recycle_enabled=True,
        ssh_key_names={"ours"},
        end_of_life=50,
        recycle_grace_period=0,
    )
    assert result.action == "claimed"
    provider.power_off_server.assert_not_called()
    provider.delete_server.assert_not_called()


@TestScenario
def reusable_cloud_server_is_parked(self):
    server = _server()
    provider = _provider(server)
    provider.is_recycled_server.return_value = False
    result = retire_to_recycle_pool(
        provider,
        server,
        recycle_enabled=True,
        ssh_key_names={"ours"},
        end_of_life=50,
        recycle_grace_period=0,
    )
    assert result.action == "pooled"
    provider.power_off_server.assert_called_once_with(server)
    assert provider.update_server.call_args.kwargs["name"].startswith(
        recycle_server_name_prefix
    )


@TestScenario
def no_reimage_activation_runs_cleanup(self):
    server = _server(f"{recycle_server_name_prefix}one", CloudProvider.STATUS_OFF)
    provider = _provider(server)
    acquired = activate_recycled_server(provider, _claim(server), rebuild=False)
    assert acquired.use_recycle_script is True
    provider.power_on_server.assert_called_once()
    provider.rebuild_server.assert_not_called()
    provider.update_server.assert_called_once()


@TestScenario
def activation_replaces_stale_runner_labels(self):
    server = _server(f"{recycle_server_name_prefix}one", CloudProvider.STATUS_OFF)
    server.labels.update(
        {"runner-label-0": "old", "runner-label-1": "stale", "metadata": "keep"}
    )
    claim = _claim(server)
    claim.request.labels["runner-label-0"] = "new"
    provider = _provider(server)
    activate_recycled_server(provider, claim, rebuild=False)
    labels = provider.update_server.call_args.kwargs["labels"]
    assert labels["runner-label-0"] == "new"
    assert "runner-label-1" not in labels
    assert labels["metadata"] == "keep"


@TestScenario
def hetzner_reimage_activation_skips_cleanup(self):
    server = _server(f"{recycle_server_name_prefix}one", CloudProvider.STATUS_OFF)
    provider = _provider(server)
    acquired = activate_recycled_server(provider, _claim(server), rebuild=True)
    assert acquired.use_recycle_script is False
    provider.rebuild_server.assert_called_once_with(server, "image-a")
    provider.power_on_server.assert_not_called()


@TestScenario
def scaleway_activation_failure_deletes_candidate(self):
    server = _server(f"{recycle_server_name_prefix}one", CloudProvider.STATUS_OFF)
    provider = _provider(server)
    provider.power_on_server.side_effect = RuntimeError("capacity unavailable")
    acquired = activate_recycled_server(
        provider,
        _claim(server),
        rebuild=False,
        delete_on_activation_failure=True,
    )
    assert acquired is None
    provider.delete_server.assert_called_once_with(server)
    provider.update_server.assert_not_called()


@TestScenario
def metadata_commit_failure_is_compensated(self):
    server = _server(f"{recycle_server_name_prefix}one", CloudProvider.STATUS_OFF)
    provider = _provider(server)
    provider.update_server.side_effect = RuntimeError("metadata update failed")
    try:
        activate_recycled_server(provider, _claim(server), rebuild=False)
    except RuntimeError:
        pass
    else:
        assert False, "metadata failure was not propagated"
    provider.power_on_server.assert_called_once()
    provider.power_off_server.assert_called_once_with(server)


@TestScenario
def aws_style_default_retirement_deletes_owned_server(self):
    server = _server()
    provider = MagicMock()
    provider.has_matching_ssh_key.return_value = True
    result = CloudProvider.retire_runner_server(
        provider,
        server,
        reason="unused",
        recycle_enabled=True,
        ssh_key_names={"ours"},
        end_of_life=50,
        recycle_grace_period=0,
    )
    assert result.action == "deleted"
    provider.delete_server.assert_called_once_with(server)


@TestScenario
def static_retirement_releases_lease(self):
    server = _server()
    provider = object.__new__(DedicatedStaticCloudProvider)
    provider.delete_server = MagicMock()
    result = provider.retire_runner_server(
        server,
        reason="unused",
        recycle_enabled=True,
        ssh_key_names=set(),
        end_of_life=50,
        recycle_grace_period=0,
    )
    assert result.action == "released"
    provider.delete_server.assert_called_once_with(server)


@TestScenario
def one_recycled_server_can_only_be_claimed_once(self):
    server = _server(f"{recycle_server_name_prefix}one", CloudProvider.STATUS_OFF)
    provider = object.__new__(HetznerCloudProvider)
    provider._recycle_with_rebuild = False
    provider.list_runner_servers = MagicMock(return_value=[server])
    provider.is_recycled_server = MagicMock(return_value=True)
    provider.has_matching_ssh_key = MagicMock(return_value=True)
    request = _claim(server).request
    first = provider.claim_recycled_server(request)
    second = provider.claim_recycled_server(request)
    assert first is not None
    assert second is None
    provider.release_recycle_claim(first)
    assert provider.claim_recycled_server(request) is not None


@TestScenario
def supplied_recycle_inventory_avoids_provider_list_call(self):
    server = _server(f"{recycle_server_name_prefix}one", CloudProvider.STATUS_OFF)
    provider = object.__new__(HetznerCloudProvider)
    provider._recycle_with_rebuild = False
    provider.list_runner_servers = MagicMock()
    provider.is_recycled_server = MagicMock(return_value=True)
    provider.has_matching_ssh_key = MagicMock(return_value=True)
    request = replace(_claim(server).request, candidates=(server,))
    assert provider.claim_recycled_server(request) is not None
    provider.list_runner_servers.assert_not_called()


@TestScenario
def end_of_life_server_respects_grace_then_deletes(self):
    server = _server(f"{recycle_server_name_prefix}one", CloudProvider.STATUS_OFF)
    server.created = datetime.now(timezone.utc) - timedelta(minutes=51)
    server.labels[recycle_timestamp_label] = str(int(time.time()) - 120)
    provider = _provider(server)
    provider.is_recycled_server.return_value = True
    provider.get_server_tag.return_value = server.labels[recycle_timestamp_label]
    result = retire_to_recycle_pool(
        provider,
        server,
        recycle_enabled=True,
        ssh_key_names={"ours"},
        end_of_life=50,
        recycle_grace_period=60,
    )
    assert result.action == "deleted"
    provider.delete_server.assert_called_once_with(server)


@TestFeature
@Name("recycling lifecycle")
def feature(self):
    for scenario in loads(current_module(), Scenario):
        scenario()
