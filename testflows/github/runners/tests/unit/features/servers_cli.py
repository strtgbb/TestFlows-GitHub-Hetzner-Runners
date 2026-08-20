"""Tests for the multicloud server-management helpers in servers.py.

The fan-out, selection, and delete-routing logic is pure once
``provider_factory`` is patched; GitHub is not involved in these helpers.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from testflows.core import *

from testflows.github.runners import servers as servers_mod
from testflows.github.runners.cloud_provider import ProviderServer


def _server(name, id, location="loc"):
    return ProviderServer(
        id=id,
        name=name,
        status="running",
        public_ipv4="1.2.3.4",
        private_ipv4=None,
        labels={},
        server_type="cx22",
        location=location,
        created=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def _fake_provider(name, servers):
    p = MagicMock()
    p.name = name
    p.list_runner_servers.return_value = list(servers)
    return p


# ---------------------------------------------------------------------------
# _runner_servers — fan-out across providers
# ---------------------------------------------------------------------------


@TestScenario
def runner_servers_fans_out_and_pairs_with_provider(self):
    """Each active runner server is paired with the provider that owns it."""
    hz = _fake_provider("hetzner", [_server("github-runner-1-1-cx22", 1)])
    aws = _fake_provider("aws", [_server("github-runner-2-2-t3.micro", "i-abc")])
    with patch.object(servers_mod, "provider_factory", return_value=[hz, aws]):
        pairs = servers_mod._runner_servers(SimpleNamespace())
    assert [(s.name, p.name) for s, p in pairs] == [
        ("github-runner-1-1-cx22", "hetzner"),
        ("github-runner-2-2-t3.micro", "aws"),
    ], pairs


# ---------------------------------------------------------------------------
# _select — filtering semantics
# ---------------------------------------------------------------------------


@TestScenario
def select_all_returns_everything(self):
    """select_all takes every server and runner."""
    hz = _fake_provider("hetzner", [])
    pairs = [(_server("a", 1), hz), (_server("b", 2), hz)]
    runners = [SimpleNamespace(name="a", id=10)]
    sp, sr = servers_mod._select(
        pairs, runners, names=None, server_names=None, ids=None, select_all=True
    )
    assert len(sp) == 2 and len(sr) == 1, (sp, sr)


@TestScenario
def select_nothing_when_no_filters_and_not_all(self):
    """No filters and select_all False selects nothing — the delete guardrail."""
    hz = _fake_provider("hetzner", [])
    pairs = [(_server("a", 1), hz)]
    runners = [SimpleNamespace(name="a", id=1)]
    sp, sr = servers_mod._select(
        pairs, runners, names=None, server_names=None, ids=None, select_all=False
    )
    assert sp == [] and sr == [], (sp, sr)


@TestScenario
def select_by_name_prefix(self):
    """--name matches servers and runners by name prefix."""
    hz = _fake_provider("hetzner", [])
    s1, s2 = _server("github-runner-1-1-cx22", 1), _server("other-9", 9)
    pairs = [(s1, hz), (s2, hz)]
    runners = [
        SimpleNamespace(name="github-runner-1-1-cx22", id=1),
        SimpleNamespace(name="other-9", id=9),
    ]
    sp, sr = servers_mod._select(
        pairs, runners, names=["github-runner-1"], server_names=None, ids=None,
        select_all=False,
    )
    assert [s.name for s, _ in sp] == ["github-runner-1-1-cx22"], sp
    assert [r.name for r in sr] == ["github-runner-1-1-cx22"], sr


@TestScenario
def select_by_id_matches_string_ids_and_pulls_runner(self):
    """--id matches server ids as strings (so AWS/Scaleway ids work) and pulls
    the runner that shares the matched server's name."""
    hz = _fake_provider("hetzner", [])
    s = _server("github-runner-2-2-t3", "i-abc")
    pairs = [(s, hz)]
    runners = [SimpleNamespace(name="github-runner-2-2-t3", id=2)]
    sp, sr = servers_mod._select(
        pairs, runners, names=None, server_names=None, ids=["i-abc"],
        select_all=False,
    )
    assert [s.name for s, _ in sp] == ["github-runner-2-2-t3"], sp
    assert [r.name for r in sr] == ["github-runner-2-2-t3"], sr


@TestScenario
def select_dedups_overlapping_filters(self):
    """A server matched by two filters is selected once, not twice."""
    hz = _fake_provider("hetzner", [])
    s = _server("github-runner-1-1-cx22", 1)
    pairs = [(s, hz)]
    runners = [SimpleNamespace(name="github-runner-1-1-cx22", id=1)]
    sp, sr = servers_mod._select(
        pairs, runners,
        names=["github-runner-1"],
        server_names=["github-runner-1-1-cx22"],
        ids=None,
        select_all=False,
    )
    assert len(sp) == 1, sp
    assert len(sr) == 1, sr


# ---------------------------------------------------------------------------
# _delete_servers — routing
# ---------------------------------------------------------------------------


@TestScenario
def delete_routes_each_server_to_its_owning_provider(self):
    """Each server is deleted through the provider it came from, not another."""
    hz, aws = _fake_provider("hetzner", []), _fake_provider("aws", [])
    s_hz = _server("github-runner-1-1-cx22", 1)
    s_aws = _server("github-runner-2-2-t3", "i-abc")
    servers_mod._delete_servers([(s_hz, hz), (s_aws, aws)])
    hz.delete_server.assert_called_once_with(s_hz)
    aws.delete_server.assert_called_once_with(s_aws)


# ---------------------------------------------------------------------------
# Feature entry point
# ---------------------------------------------------------------------------


@TestFeature
@Name("servers cli")
def feature(self):
    """Multicloud server-management helpers in servers.py."""
    for scenario in loads(current_module(), Scenario):
        scenario()
