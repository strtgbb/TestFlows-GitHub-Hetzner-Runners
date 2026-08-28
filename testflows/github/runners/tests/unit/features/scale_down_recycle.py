"""Tests that scale_down's recycle path dispatches through the CloudProvider
abstraction (provider-agnostic), rather than calling native cloud SDK methods.

These lock in the Phase 1 refactor (provider-abstracted recycle path) and the
provider-aware SSH-key ownership check: a server is only recycled/deleted after
verifying its stored SSH-key name (read via the provider's own tag) matches one
of this controller's keys for that provider.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from testflows.core import *

from testflows.github.runners.cloud_provider import ProviderServer, RetirementResult
from testflows.github.runners.scale_down import (
    delete_recyclable_server,
    recycle_server,
    unused_runner_action,
)
from testflows.github.runners.constants import (
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
    provider = MagicMock(recycle_grace_period=None)
    provider.name = name
    provider.has_matching_ssh_key.side_effect = (
        lambda server, key_names: stored_ssh_key_name in key_names
    )
    provider.is_recycle_claimed.return_value = False
    provider.retire_runner_server.return_value = RetirementResult(
        "pooled", "server"
    )
    return provider




def _ssh_keys(*names, provider_name="hetzner"):
    keys = []
    for n in names:
        k = MagicMock()
        k.name = n
        keys.append(k)
    return {provider_name: keys}


@TestScenario
def retirement_is_delegated_to_provider(self):
    """Scale-down delegates the complete retirement transition."""
    provider = _provider(stored_ssh_key_name="mykey")
    server = _server("github-runner-1-0-cx22")
    with When("recycle_server runs on a fresh, owned, non-recycle server"):
        recycle_server(
            reason="powered_off",
            server=server,
            provider=provider,
            ssh_key_names={"mykey"},
            end_of_life=60,
            recycle_grace_period=0,
        )
    with Then("it passes policy and ownership context to the provider"):
        provider.retire_runner_server.assert_called_once_with(
            server,
            reason="powered_off",
            recycle_enabled=True,
            ssh_key_names={"mykey"},
            end_of_life=60,
            recycle_grace_period=0,
        )


@TestScenario
def retirement_failure_records_metric(self):
    provider = _provider(stored_ssh_key_name="mykey")
    provider.retire_runner_server.side_effect = RuntimeError("delete failed")
    server = _server("github-runner-1-0-cx22")
    with patch(
        "testflows.github.runners.scale_down.metrics.record_scale_down_failure"
    ) as record_failure:
        result = recycle_server(
            reason="zombie",
            server=server,
            provider=provider,
            ssh_key_names={"mykey"},
            end_of_life=50,
            recycle_grace_period=0,
        )
    assert result.action == "failed"
    record_failure.assert_called_once()
    assert record_failure.call_args.kwargs["error_type"] == "retire_zombie_failed"


@TestScenario
def unmanaged_retirement_is_logged(self):
    """An unmanaged (unowned) retirement emits a diagnostic naming the mismatch.

    Regression guard: a silent 'unmanaged' result is what turned a fleet-wide
    ownership break into an invisible SBS-quota deadlock. The wrapper must log
    the stored ssh-key vs the controller's owned keys.
    """
    provider = _provider(stored_ssh_key_name="theirkey")
    provider.retire_runner_server.return_value = RetirementResult("unmanaged", "server")
    provider.get_server_ssh_key_name.return_value = "theirkey"
    server = _server("github-runner-1-0-cx22")
    with When("the provider reports the server unmanaged"), patch(
        "testflows.github.runners.scale_down.Action"
    ) as action:
        result = recycle_server(
            reason="powered_off",
            server=server,
            provider=provider,
            ssh_key_names={"mykey"},
            end_of_life=60,
            recycle_grace_period=0,
        )
    with Then("it returns unmanaged and logs the stored key vs owned keys"):
        assert result.action == "unmanaged"
        messages = " ".join(str(c.args[0]) for c in action.call_args_list if c.args)
        assert "unmanaged" in messages, messages
        assert "theirkey" in messages, messages  # server's stored ssh-key
        assert "mykey" in messages, messages  # controller's owned keys


@TestScenario
def managed_retirement_is_not_logged_as_unmanaged(self):
    """A normal (owned) retirement does not emit the unmanaged diagnostic."""
    provider = _provider(stored_ssh_key_name="mykey")  # default result is "pooled"
    server = _server("github-runner-1-0-cx22")
    with When("recycle_server runs on an owned server"), patch(
        "testflows.github.runners.scale_down.Action"
    ) as action:
        recycle_server(
            reason="powered_off",
            server=server,
            provider=provider,
            ssh_key_names={"mykey"},
            end_of_life=60,
            recycle_grace_period=0,
        )
    with Then("no unmanaged diagnostic is logged"):
        messages = " ".join(str(c.args[0]) for c in action.call_args_list if c.args)
        assert "unmanaged" not in messages, messages


@TestScenario
def found_server_is_recycled(self):
    """A resolved server takes the recycle path regardless of runner status."""
    server = _server("github-runner-1-0-cx22")
    for status in ("online", "offline"):
        assert unused_runner_action(server, status) == "recycle", status


@TestScenario
def offline_runner_without_server_is_deregistered(self):
    """A dead (offline) runner whose server is gone is deregistered.

    Regression guard: reaped ephemeral runners never self-deregister and
    GitHub's offline cleanup takes weeks, so scale_down must remove them.
    """
    assert unused_runner_action(None, "offline") == "deregister"


@TestScenario
def online_runner_without_server_is_left_alone(self):
    """An online runner with no server is left alone, never deregistered.

    Regression guard: this is the case that broke fleets — an idle/waiting
    (or another controller's) runner must not be deregistered on a lookup miss.
    """
    assert unused_runner_action(None, "online") == "leave"


@TestScenario
def delete_recyclable_resolves_provider_per_server(self):
    """delete_recyclable_server deletes the picked server via its own provider."""
    provider = MagicMock(recycle_grace_period=None)
    provider.is_recycle_claimed.return_value = False
    provider.reserve_recycled_server.return_value = True
    s1 = _server(f"{recycle_server_name_prefix}one")
    s2 = _server(f"{recycle_server_name_prefix}two")
    with When("delete_recyclable_server is called with ProviderServers"):
        deleted = delete_recyclable_server(
            server_name="github-runner-9-0-cx22",
            recyclable_servers=[(s1, provider), (s2, provider)],
            provider_prices={},  # random pick
            recycle_grace_period=0,
        )
    with Then("exactly one server is deleted through the provider"):
        assert provider.delete_server.call_count == 1, provider.delete_server.call_count
        assert deleted in (s1.name, s2.name), deleted


@TestScenario
def delete_recyclable_skips_already_reserved_servers(self):
    """A candidate another worker already reserved is skipped for the next one."""
    provider = MagicMock(recycle_grace_period=None)
    provider.is_recycle_claimed.return_value = False
    # First reservation attempt loses the race; the next one wins.
    provider.reserve_recycled_server.side_effect = [False, True]
    s1 = _server(f"{recycle_server_name_prefix}one")
    s2 = _server(f"{recycle_server_name_prefix}two")
    with When("one candidate cannot be reserved"):
        deleted = delete_recyclable_server(
            server_name="github-runner-9-0-cx22",
            recyclable_servers=[(s1, provider), (s2, provider)],
            provider_prices={},
            recycle_grace_period=0,
        )
    with Then("it falls through to a candidate it can reserve"):
        assert provider.delete_server.call_count == 1, provider.delete_server.call_count
        assert deleted in (s1.name, s2.name), deleted


@TestScenario
def delete_recyclable_returns_none_when_all_reserved(self):
    """If every candidate is reserved, nothing is deleted."""
    provider = MagicMock(recycle_grace_period=None)
    provider.is_recycle_claimed.return_value = False
    provider.reserve_recycled_server.return_value = False
    s1 = _server(f"{recycle_server_name_prefix}one")
    with When("no candidate can be reserved"):
        deleted = delete_recyclable_server(
            server_name="github-runner-9-0-cx22",
            recyclable_servers=[(s1, provider)],
            provider_prices={},
            recycle_grace_period=0,
        )
    with Then("nothing is deleted and no name is returned"):
        assert deleted is None
        provider.delete_server.assert_not_called()


@TestScenario
def delete_recyclable_excludes_claimed_servers(self):
    """A server currently being activated (claimed) is never chosen for deletion."""
    provider = MagicMock(recycle_grace_period=None)
    provider.reserve_recycled_server.return_value = True
    claimed = _server(f"{recycle_server_name_prefix}claimed")
    free = _server(f"{recycle_server_name_prefix}free")
    provider.is_recycle_claimed.side_effect = lambda s: s.name == claimed.name
    with When("one of the candidates is reserved for activation"):
        deleted = delete_recyclable_server(
            server_name="github-runner-9-0-cx22",
            recyclable_servers=[(claimed, provider), (free, provider)],
            provider_prices={},
            recycle_grace_period=0,
        )
    with Then("only the unclaimed server is eligible for deletion"):
        assert deleted == free.name, deleted
        provider.delete_server.assert_called_once_with(free)


@TestScenario
def delete_recyclable_respects_per_provider_grace_override(self):
    """A provider's own recycle_grace_period overrides the passed global grace.

    A server recycled 100s ago is inside a provider's 300s grace, so eviction
    retains it even though the 60s global grace has elapsed.
    """
    import time as _time

    provider = MagicMock(recycle_grace_period=None)
    provider.name = "scaleway"
    provider.recycle_grace_period = 300
    provider.is_recycle_claimed.return_value = False
    provider.reserve_recycled_server.return_value = True
    provider.get_server_tag.return_value = str(int(_time.time()) - 100)
    s = _server(f"{recycle_server_name_prefix}one")
    with When("eviction runs with a 60s global grace"):
        deleted = delete_recyclable_server(
            server_name="github-runner-9-0-cx22",
            recyclable_servers=[(s, provider)],
            provider_prices={},
            recycle_grace_period=60,
        )
    with Then("the provider's longer grace retains it"):
        assert deleted is None, deleted
        provider.delete_server.assert_not_called()


@TestScenario
def delete_recyclable_provider_grace_applies_when_global_disabled(self):
    """A provider's grace still holds when the global grace is disabled (0).

    Regression: the grace check used to sit behind `if global > 0`, so a
    provider override was bypassed whenever the global grace was 0 or None and
    the candidate could be deleted too early.
    """
    import time as _time

    provider = MagicMock(recycle_grace_period=None)
    provider.name = "scaleway"
    provider.recycle_grace_period = 300
    provider.is_recycle_claimed.return_value = False
    provider.reserve_recycled_server.return_value = True
    provider.get_server_tag.return_value = str(int(_time.time()) - 100)
    s = _server(f"{recycle_server_name_prefix}one")
    with When("eviction runs with the global grace disabled (0)"):
        deleted = delete_recyclable_server(
            server_name="github-runner-9-0-cx22",
            recyclable_servers=[(s, provider)],
            provider_prices={},
            recycle_grace_period=0,
        )
    with Then("the provider's own grace still retains it"):
        assert deleted is None, deleted
        provider.delete_server.assert_not_called()


@TestScenario
def delete_recyclable_deletes_when_no_grace_anywhere(self):
    """Global disabled and no provider override means immediate eligibility."""
    provider = MagicMock(recycle_grace_period=None)
    provider.name = "scaleway"
    provider.recycle_grace_period = None
    provider.is_recycle_claimed.return_value = False
    provider.reserve_recycled_server.return_value = True
    s = _server(f"{recycle_server_name_prefix}one")
    deleted = delete_recyclable_server(
        server_name="github-runner-9-0-cx22",
        recyclable_servers=[(s, provider)],
        provider_prices={},
        recycle_grace_period=0,
    )
    with Then("the candidate is deleted without stamping a recycle timestamp"):
        assert deleted == s.name, deleted
        provider.delete_server.assert_called_once_with(s)
        provider.set_server_tags.assert_not_called()


@TestScenario
def delete_recyclable_uses_global_grace_without_override(self):
    """With no provider override, the passed global grace applies."""
    import time as _time

    provider = MagicMock(recycle_grace_period=None)
    provider.name = "scaleway"
    provider.recycle_grace_period = None
    provider.is_recycle_claimed.return_value = False
    provider.reserve_recycled_server.return_value = True
    provider.get_server_tag.return_value = str(int(_time.time()) - 100)
    s = _server(f"{recycle_server_name_prefix}one")
    deleted = delete_recyclable_server(
        server_name="github-runner-9-0-cx22",
        recyclable_servers=[(s, provider)],
        provider_prices={},
        recycle_grace_period=60,
    )
    assert deleted == s.name, deleted
    provider.delete_server.assert_called_once_with(s)


@TestScenario
def delete_recyclable_falls_back_to_random_across_currencies(self):
    """Mixed provider currencies disable price comparison and force a random pick."""
    p_eur = MagicMock()
    p_eur.name = "hetzner"
    p_usd = MagicMock()
    p_usd.name = "aws"
    for p in (p_eur, p_usd):
        p.recycle_grace_period = None
        p.is_recycle_claimed.return_value = False
        p.reserve_recycled_server.return_value = True
    s1 = _server(f"{recycle_server_name_prefix}one")
    s2 = _server(f"{recycle_server_name_prefix}two")
    prices = {
        "hetzner": {"currency": "EUR", "prices": {"cx22": {"nbg1": 5.0}}},
        "aws": {"currency": "USD", "prices": {"cx22": {"nbg1": 5.0}}},
    }
    with When("candidates span two currencies"), patch(
        "testflows.github.runners.scale_down.random.shuffle"
    ) as shuffle:
        delete_recyclable_server(
            server_name="github-runner-9-0-cx22",
            recyclable_servers=[(s1, p_eur), (s2, p_usd)],
            provider_prices=prices,
            recycle_grace_period=0,
        )
    with Then("prices are not compared across currencies (random pick)"):
        shuffle.assert_called_once()


@TestScenario
def delete_recyclable_uses_price_order_within_one_currency(self):
    """A single currency keeps the cheapest ordering rather than shuffling."""
    provider = MagicMock(recycle_grace_period=None)
    provider.name = "hetzner"
    provider.is_recycle_claimed.return_value = False
    provider.reserve_recycled_server.return_value = True
    s1 = _server(f"{recycle_server_name_prefix}one")
    s2 = _server(f"{recycle_server_name_prefix}two")
    prices = {"hetzner": {"currency": "EUR", "prices": {"cx22": {"nbg1": 5.0}}}}
    with When("all candidates share one currency"), patch(
        "testflows.github.runners.scale_down.random.shuffle"
    ) as shuffle:
        delete_recyclable_server(
            server_name="github-runner-9-0-cx22",
            recyclable_servers=[(s1, provider), (s2, provider)],
            provider_prices=prices,
            recycle_grace_period=0,
        )
    with Then("the cheapest ordering is used, not a shuffle"):
        shuffle.assert_not_called()


@TestFeature
@Name("scale_down recycle")
def feature(self):
    """scale_down recycle path is provider-agnostic with provider-aware key checks."""
    for scenario in loads(current_module(), Scenario):
        scenario()
