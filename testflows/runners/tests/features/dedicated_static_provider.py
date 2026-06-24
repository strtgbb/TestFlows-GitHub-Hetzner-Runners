"""Tests for DedicatedStaticCloudProvider's durable claim-marker lease system.

The provider stakes a host-side claim marker (`~/.github-runner/claim`) before
setup so an in-flight host is not double-dispatched, with the marker's mtime as
the durable source of truth. All SSH is mocked at the provider's `ssh` boundary
(it returns the remote *exit code*, not output), so no real hosts are touched:

    find  -> 11 free/stale/absent, 10 fresh claim present, 255 unreachable
    touch -> 0 claimed ok
    rm -f -> 0 cleared ok
"""
from unittest.mock import patch

from testflows.core import *

from testflows.runners.cloud_provider import ProviderServer, ProviderServerType
import testflows.runners.providers.dedicated_static.provider as provider_mod
from testflows.runners.providers.dedicated_static.provider import (
    DedicatedStaticCloudProvider,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TYPE = ProviderServerType(name="x")


def _provider(hosts=("1.2.3.4",), claim_timeout=360):
    groups = {"g1": {"labels": ["type-x", "in-y"], "hosts": list(hosts)}}
    return DedicatedStaticCloudProvider(groups, claim_timeout=claim_timeout)


def _claim(provider, name="github-runner-abc"):
    return provider.create_server(
        name=name, server_type=_TYPE, location="y",
        image=None, ssh_keys=[], labels={},
    )


def _ssh_by_code(find=11, touch=0, rm=0, per_host_find=None):
    """Return a fake ssh(server, cmd, ...) that maps command -> exit code.

    per_host_find: optional {ip: find_rc} to vary the find result by host.
    """
    def _ssh(server, cmd, *args, **kwargs):
        if "find " in cmd:
            if per_host_find is not None:
                return per_host_find.get(server.public_ipv4, find)
            return find
        if "touch" in cmd:
            return touch
        if "rm -f" in cmd:
            return rm
        return 0
    return _ssh


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@TestScenario
def claim_window_minutes_from_timeout(self):
    """The find -mmin window is ceil(claim_timeout / 60), at least 1 minute."""
    assert _provider(claim_timeout=360)._claim_minutes() == 6
    assert _provider(claim_timeout=61)._claim_minutes() == 2
    assert _provider(claim_timeout=1)._claim_minutes() == 1


@TestScenario
def free_host_is_claimed_and_marker_written(self):
    """A host with no fresh marker is claimed and its marker is touched."""
    prov = _provider()
    cmds = []

    def _ssh(server, cmd, *a, **k):
        cmds.append(cmd)
        return 11 if "find " in cmd else 0

    with patch.object(provider_mod, "ssh", _ssh):
        srv = _claim(prov)

    assert srv is not None
    assert prov._hosts[0].lease_name == "github-runner-abc"
    assert any("touch" in c for c in cmds), "claim marker must be written"


@TestScenario
def fresh_marker_host_is_skipped_then_raises(self):
    """A host with a fresh marker (busy/orphaned) is not claimed; with no other
    host available create_server raises and the optimistic lease is rolled back."""
    prov = _provider()
    with patch.object(provider_mod, "ssh", _ssh_by_code(find=10)):
        try:
            _claim(prov)
            raise AssertionError("expected create_server to raise")
        except Exception as e:
            assert "no idle dedicated host" in str(e), e
    assert prov._hosts[0].lease_name is None, "optimistic lease must be rolled back"


@TestScenario
def unreachable_host_is_not_claimed(self):
    """An unreachable host (ssh exit 255 on the probe) is never claimed."""
    prov = _provider()
    with patch.object(provider_mod, "ssh", _ssh_by_code(find=255)):
        try:
            _claim(prov)
            raise AssertionError("expected create_server to raise")
        except Exception as e:
            assert "no idle dedicated host" in str(e), e
    assert prov._hosts[0].lease_name is None


@TestScenario
def busy_host_skipped_next_free_claimed(self):
    """With one busy and one free host, the busy one is skipped and the free
    one is claimed."""
    prov = _provider(hosts=("1.2.3.4", "5.6.7.8"))
    fake = _ssh_by_code(per_host_find={"1.2.3.4": 10, "5.6.7.8": 11})
    with patch.object(provider_mod, "ssh", fake):
        srv = _claim(prov, name="github-runner-def")
    assert srv.public_ipv4 == "5.6.7.8", srv.public_ipv4
    assert prov._hosts[0].lease_name is None
    assert prov._hosts[1].lease_name == "github-runner-def"


@TestScenario
def release_on_success_clears_marker_keeps_lease(self):
    """release_claim(succeeded=True) clears the marker but keeps the in-memory
    lease (the registered runner becomes the lease signal)."""
    prov = _provider()
    with patch.object(provider_mod, "ssh", _ssh_by_code(find=11)):
        srv = _claim(prov)
        cmds = []

        def _ssh(server, cmd, *a, **k):
            cmds.append(cmd)
            return 0

        with patch.object(provider_mod, "ssh", _ssh):
            prov.release_claim(srv, succeeded=True)

    assert any("rm -f" in c for c in cmds), "marker must be cleared on success"
    assert prov._hosts[0].lease_name == "github-runner-abc", "lease kept on success"


@TestScenario
def release_on_failure_clears_marker_and_frees_lease(self):
    """release_claim(succeeded=False) clears the marker AND frees the lease."""
    prov = _provider()
    with patch.object(provider_mod, "ssh", _ssh_by_code(find=11)):
        srv = _claim(prov)
        prov.release_claim(srv, succeeded=False)
    assert prov._hosts[0].lease_name is None, "lease must be freed on failure"


@TestScenario
def release_claim_is_noop_for_foreign_server(self):
    """release_claim ignores a server that is not one of this provider's hosts."""
    prov = _provider()
    foreign = ProviderServer(
        id="x", name="other", status="running",
        public_ipv4=None, private_ipv4=None, labels={},
        server_type="t", location="l", created=None, _native=None,
    )
    called = []
    with patch.object(provider_mod, "ssh", lambda *a, **k: called.append(1)):
        prov.release_claim(foreign, succeeded=False)  # must not raise
    assert called == [], "no SSH should be issued for a foreign server"


@TestScenario
def reconcile_confirms_active_and_clears_others(self):
    """reconcile_runner_leases sets a host's lease to its static_name when its
    runner is registered, and clears any host without a live runner."""
    prov = _provider(hosts=("1.2.3.4", "5.6.7.8"))
    h0, h1 = prov._hosts
    prov._set_lease(h0, "github-runner-old0")
    prov._set_lease(h1, "github-runner-old1")

    prov.reconcile_runner_leases({h0.static_name})

    assert h0.lease_name == h0.static_name, "registered runner -> active lease"
    assert h1.lease_name is None, "no live runner -> lease cleared"


@TestScenario
def label_prefix_aware_type_and_location(self):
    """With a label_prefix set, the provider must extract type/location from the
    prefixed `<prefix>type-*` / `<prefix>in-*` labels (not a bare `type-`), so
    supported types are recognized and a matching job leases a host."""
    groups = {
        "g1": {
            "labels": ["altinity-type-x", "altinity-in-y", "altinity-self-hosted"],
            "hosts": ["1.2.3.4"],
        }
    }
    prov = DedicatedStaticCloudProvider(
        groups, claim_timeout=360, label_prefix="altinity-"
    )
    with Then("the prefixed type is recognized as supported"):
        assert prov.get_server_type("x").name == "x"
    with And("a job for that type+location leases the host"):
        with patch.object(provider_mod, "ssh", _ssh_by_code(find=11)):
            srv = prov.create_server(
                name="github-runner-abc", server_type=ProviderServerType("x"),
                location="y", image=None, ssh_keys=[], labels={},
            )
        assert srv is not None
        assert prov._hosts[0].lease_name == "github-runner-abc"


# ---------------------------------------------------------------------------
# Feature entry point
# ---------------------------------------------------------------------------


@TestFeature
@Name("dedicated static provider")
def feature(self):
    """Durable claim-marker lease behavior for DedicatedStaticCloudProvider."""
    for scenario in loads(current_module(), Scenario):
        scenario()
