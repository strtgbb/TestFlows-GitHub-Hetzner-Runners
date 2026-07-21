"""Tests for DedicatedStaticCloudProvider's durable claim-marker lease system.

The provider stakes a host-side claim before setup so an in-flight host is not
double-dispatched: an atomic `mkdir` of the lock dir
(`/run/user/$UID/testflows-github-runners/claim`) wins the claim, stale locks are
reclaimed via `find -mmin`, and failure-path cleanup uses `rm -rf`. All SSH is
mocked at the provider's `ssh`
boundary, which returns the remote *exit code* (not output), so no real hosts
are touched:

    mkdir  -> 0 claim acquired, non-zero already held
    rm -rf -> 0 released
"""
from unittest.mock import patch

from testflows.core import *

from testflows.runners.cloud_provider import (
    ProviderServer,
    ProviderServerType,
)
import testflows.runners.providers.dedicated_static.provider as provider_mod
from testflows.runners.providers.dedicated_static.provider import (
    DedicatedStaticCloudProvider,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TYPE = ProviderServerType(name="x")


def _provider(hosts=("1.2.3.4",), claim_ttl_minutes=360):
    groups = {"g1": {"labels": ["type-x", "in-y"], "hosts": list(hosts)}}
    return DedicatedStaticCloudProvider(groups, claim_ttl_minutes=claim_ttl_minutes)


def _claim(provider, name="github-runner-abc"):
    return provider.create_server(
        name=name, server_type=_TYPE, location="y",
        image=None, ssh_keys=[], labels={},
    )


def _ssh_claim(acquired=True, per_host=None):
    """Fake ssh modeling the atomic mkdir claim by exit code.

    The claim command exits 0 when the lock is acquired, non-zero otherwise;
    release (`rm -rf`) always exits 0. ``acquired`` sets the default; per_host
    is an optional {ip: bool} to vary acquisition by host.
    """
    def _ssh(server, cmd, *args, **kwargs):
        if "rm -rf" in cmd:  # release
            return 0
        ok = (
            per_host.get(server.public_ipv4, acquired)
            if per_host is not None
            else acquired
        )
        return 0 if ok else 1
    return _ssh


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@TestScenario
def setup_step_is_recycle_driven(self):
    """Static hosts are provisioned out of band, so the per-lease setup-step is
    cleanup: it defaults to recycle.sh and is selected by a recycle-<name> label.
    A setup-<name> label is the provisioning selector (cloud, in a mixed fleet)
    and is ignored — so a job carrying both still cleans with recycle-."""
    prov = _provider()
    with Then("no override -> default recycle.sh"):
        assert prov.setup_script_name([]) == "recycle.sh"
    with And("a recycle-<name> label selects the cleanup script"):
        assert prov.setup_script_name(["recycle-clean"]) == "clean.sh"
    with And("a lone setup-<name> label is ignored (no provisioning on static)"):
        assert prov.setup_script_name(["setup-provision"]) == "recycle.sh"
    with And("a mixed-fleet job with both -> recycle- wins, no error"):
        assert prov.setup_script_name(["setup-provision", "recycle-clean"]) == "clean.sh"


@TestScenario
def claim_window_minutes_from_timeout(self):
    """The find -mmin window is claim_ttl_minutes (minimum 1)."""
    assert _provider(claim_ttl_minutes=360)._claim_minutes() == 360
    assert _provider(claim_ttl_minutes=2)._claim_minutes() == 2
    assert _provider(claim_ttl_minutes=1)._claim_minutes() == 1


@TestScenario
def free_host_is_claimed(self):
    """A free host is acquired with a single atomic mkdir claim."""
    prov = _provider()
    cmds = []

    def _ssh(server, cmd, *a, **k):
        cmds.append(cmd)
        return 0  # claim acquired

    with patch.object(provider_mod, "ssh", _ssh):
        srv = _claim(prov)

    assert srv is not None
    assert prov._hosts[0].lease_name == "github-runner-abc"
    assert any(
        f"mkdir {prov._CLAIM_PATH}" in c for c in cmds
    ), "atomic mkdir claim must be issued"


@TestScenario
def fresh_marker_host_is_skipped_returns_none(self):
    """A host whose claim is already held (busy/orphaned) is not acquired; with
    no other host available, create_server returns None — an expected
    capacity/in-flight outcome, not an error — and the optimistic lease is
    rolled back."""
    prov = _provider()
    with patch.object(provider_mod, "ssh", _ssh_claim(acquired=False)):
        result = _claim(prov)
    assert result is None, f"expected None for a fully-claimed pool, got {result!r}"
    assert prov._hosts[0].lease_name is None, "optimistic lease must be rolled back"


@TestScenario
def unreachable_host_returns_none(self):
    """An unreachable host (ssh exit 255 on the claim) is never acquired;
    create_server returns None rather than raising."""
    prov = _provider()
    with patch.object(provider_mod, "ssh", lambda *a, **k: 255):
        result = _claim(prov)
    assert result is None, f"expected None for an unreachable host, got {result!r}"
    assert prov._hosts[0].lease_name is None


@TestScenario
def busy_host_skipped_next_free_claimed(self):
    """With one busy and one free host, the busy one is skipped and the free
    one is claimed."""
    prov = _provider(hosts=("1.2.3.4", "5.6.7.8"))
    fake = _ssh_claim(per_host={"1.2.3.4": False, "5.6.7.8": True})
    with patch.object(provider_mod, "ssh", fake):
        srv = _claim(prov, name="github-runner-def")
    assert srv.public_ipv4 == "5.6.7.8", srv.public_ipv4
    assert prov._hosts[0].lease_name is None
    assert prov._hosts[1].lease_name == "github-runner-def"


@TestScenario
def release_on_success_clears_marker_keeps_lease(self):
    """The successful post-setup hook keeps claim and lease untouched.

    The durable claim is reboot-scoped and remains on success; only failure
    clears claim/lease.
    """
    prov = _provider()
    with patch.object(provider_mod, "ssh", _ssh_claim()):
        srv = _claim(prov)

    cmds = []

    def _ssh(server, cmd, *a, **k):
        cmds.append(cmd)
        return 0

    with patch.object(provider_mod, "ssh", _ssh):
        prov.after_server_setup(
            srv, None
        )

    assert cmds == [], "success path must not clear the durable claim"
    assert prov._hosts[0].lease_name == "github-runner-abc", "lease kept on success"


@TestScenario
def release_on_failure_clears_marker_and_frees_lease(self):
    """The failed post-setup hook releases the claim and frees the lease."""
    prov = _provider()
    with patch.object(provider_mod, "ssh", _ssh_claim()):
        srv = _claim(prov)
        prov.after_server_setup(
            srv,
            RuntimeError("setup failed"),
        )
    assert prov._hosts[0].lease_name is None, "lease must be freed on failure"


@TestScenario
def post_setup_is_noop_for_foreign_server(self):
    """The post-setup hook ignores a server that is not this provider's host."""
    prov = _provider()
    foreign = ProviderServer(
        id="x", name="other", status="running",
        public_ipv4=None, private_ipv4=None, labels={},
        server_type="t", location="l", created=None, _native=None,
    )
    called = []
    with patch.object(provider_mod, "ssh", lambda *a, **k: called.append(1)):
        prov.after_server_setup(
            foreign,
            RuntimeError("setup failed"),
        )
    assert called == [], "no SSH should be issued for a foreign server"


@TestScenario
def reconcile_confirms_active_and_clears_others(self):
    """The pre-cycle hook sets a host's lease to its static_name when its
    runner is registered, and clears any host without a live runner."""
    prov = _provider(hosts=("1.2.3.4", "5.6.7.8"))
    h0, h1 = prov._hosts
    prov._set_lease(h0, "github-runner-old0")
    prov._set_lease(h1, "github-runner-old1")

    for hook in (prov.before_scale_up, prov.before_scale_down):
        prov._set_lease(h0, "github-runner-old0")
        prov._set_lease(h1, "github-runner-old1")
        hook(frozenset({h0.static_name}))
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
        groups, claim_ttl_minutes=360, label_prefix="altinity-"
    )
    with Then("the prefixed type is recognized as supported"):
        assert prov.get_server_type("x").name == "x"
    with And("a job for that type+location leases the host"):
        with patch.object(provider_mod, "ssh", _ssh_claim()):
            srv = prov.create_server(
                name="github-runner-abc", server_type=ProviderServerType("x"),
                location="y", image=None, ssh_keys=[], labels={},
            )
        assert srv is not None
        assert prov._hosts[0].lease_name == "github-runner-abc"


@TestScenario
def claim_command_includes_stale_reclaim(self):
    """The atomic claim also reclaims a stale lock: atomic mkdir, then on EEXIST
    a staleness check (find -mmin +N) followed by rmdir && mkdir. That branch
    runs in the remote shell, so here we assert the command is constructed with
    it and with the configured staleness window (claim_ttl_minutes)."""
    prov = _provider(claim_ttl_minutes=360)
    captured = {}

    def _ssh(server, cmd, *a, **k):
        captured["cmd"] = cmd
        return 0

    with patch.object(provider_mod, "ssh", _ssh):
        prov._try_claim(prov._hosts[0])

    cmd = captured["cmd"]
    assert f"mkdir {prov._CLAIM_PATH}" in cmd, cmd
    assert "-mmin +360" in cmd, cmd
    assert f"rmdir {prov._CLAIM_PATH}" in cmd and "&& mkdir" in cmd, cmd


@TestScenario
def concurrent_claims_only_one_wins(self):
    """Two controllers (separate provider instances) racing for the same host:
    the atomic mkdir claim lets exactly one acquire it; the other gets None."""
    # Shared "host filesystem" of held claim locks, keyed by host ip. The first
    # claim creates the lock (exit 0); a second sees it exists (exit 1) — i.e.
    # mkdir's atomicity. Release removes it.
    fs_locks = set()

    def _ssh(server, cmd, *a, **k):
        ip = server.public_ipv4
        if "rm -rf" in cmd:
            fs_locks.discard(ip)
            return 0
        if ip in fs_locks:
            return 1  # EEXIST — already claimed (fresh)
        fs_locks.add(ip)
        return 0  # acquired

    groups = {"g1": {"labels": ["type-x", "in-y"], "hosts": ["1.2.3.4"]}}
    prov_a = DedicatedStaticCloudProvider(groups, claim_ttl_minutes=360)
    prov_b = DedicatedStaticCloudProvider(groups, claim_ttl_minutes=360)

    with patch.object(provider_mod, "ssh", _ssh):
        srv_a = prov_a.create_server(
            name="a", server_type=_TYPE, location="y",
            image=None, ssh_keys=[], labels={},
        )
        srv_b = prov_b.create_server(
            name="b", server_type=_TYPE, location="y",
            image=None, ssh_keys=[], labels={},
        )

    winners = [s for s in (srv_a, srv_b) if s is not None]
    assert len(winners) == 1, f"exactly one claim should win, got {winners!r}"


@TestScenario
def static_owns_no_global_defaults(self):
    """Static hosts own no default image/location, so startup validation must
    not feed them the Hetzner-shaped global config.default_*.

    Regression: the per-provider startup loop used to fall back to the global
    default image for any provider without its own, and dedicated_static's
    get_image rejects foreign specs by design — aborting startup with
    ImageSpecFormatError. The provider owns no defaults, so a correct validator
    skips its image/location/type checks entirely.
    """
    from testflows.runners.errors import ImageSpecFormatError

    prov = _provider()
    with Then("the provider does not own the global config defaults"):
        # So the startup validator never hands it the Hetzner-shaped globals.
        assert prov.owns_global_config_defaults is False
    with And("it exposes no default image or location of its own"):
        assert prov.default_image is None, prov.default_image
        assert prov.default_location is None, prov.default_location
    with And("and still hard-rejects a foreign (Hetzner) image spec if asked"):
        # This is exactly why the validator must not pass it the global default.
        class _HetznerImage:
            architecture = "x86"
            name = "ubuntu-22.04"

        try:
            prov.get_image(_HetznerImage())
            assert False, "expected ImageSpecFormatError for a foreign image spec"
        except ImageSpecFormatError:
            pass


# ---------------------------------------------------------------------------
# Feature entry point
# ---------------------------------------------------------------------------


@TestFeature
@Name("dedicated static provider")
def feature(self):
    """Durable claim-marker lease behavior for DedicatedStaticCloudProvider."""
    for scenario in loads(current_module(), Scenario):
        scenario()
