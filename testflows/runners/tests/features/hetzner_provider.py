"""Tests for HetznerCloudProvider.

All hcloud I/O is mocked at the HClient level so no real API calls are made.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from testflows.core import *

from testflows.runners.cloud_provider import CloudProvider, ProviderServer
from testflows.runners.providers.hetzner.provider import HetznerCloudProvider
from testflows.runners.tests.steps.hetzner import hetzner_provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bound_server(
    id=1,
    name="github-hetzner-runner-123",
    status="running",
    labels=None,
    server_type_name="cx23",
    location_name="nbg1",
):
    """Build a minimal mock that looks like an hcloud BoundServer."""
    server = MagicMock()
    server.id = id
    server.name = name
    server.status = status
    server.labels = labels or {"github-hetzner-runner": "active"}
    server.public_net = MagicMock()
    server.public_net.ipv4 = MagicMock()
    server.public_net.ipv4.ip = "1.2.3.4"
    server.public_net.ipv6 = MagicMock()
    server.private_net = []
    server.server_type = MagicMock()
    server.server_type.name = server_type_name
    server.datacenter = MagicMock()
    server.datacenter.location = MagicMock()
    server.datacenter.location.name = location_name
    server.created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    server.volumes = []
    return server


def _provider_server(labels=None, native=None):
    return ProviderServer(
        id=1,
        name="srv",
        status="running",
        public_ipv4=None,
        private_ipv4=None,
        labels=labels or {},
        server_type="cx23",
        location="nbg1",
        created=datetime.now(timezone.utc),
        _native=native,
    )


def _fake_key_str():
    import base64
    raw = b"\x00\x00\x00\x07ssh-rsa" + b"\x00" * 20
    b64 = base64.b64encode(raw).decode()
    return f"ssh-rsa {b64} test@host"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@TestScenario
def name_is_hetzner(self):
    with Given("a Hetzner provider"):
        _, provider = hetzner_provider()
    with Then("its name is 'hetzner'"):
        assert provider.name == "hetzner"


@TestScenario
def default_specs_are_stored(self):
    """Default image/type/location/volume specs passed to __init__ are exposed
    via the provider's default_* properties (seed source for unlabeled jobs)."""
    from testflows.runners.providers.hetzner.provider import HetznerCloudProvider

    with Given("a Hetzner provider constructed with default specs"):
        provider = HetznerCloudProvider(
            token="tok",
            default_image="x86:system:ubuntu-22.04",
            default_server_type="cx23",
            default_location="nbg1",
            default_volume_size=40,
            default_volume_location="fsn1",
        )
    with Then("each default_* property returns the configured spec"):
        assert provider.default_image == "x86:system:ubuntu-22.04"
        assert provider.default_server_type == "cx23"
        assert provider.default_location == "nbg1"
        assert provider.default_volume_size == 40
        assert provider.default_volume_location == "fsn1"


@TestScenario
def name_is_string(self):
    with Given("a Hetzner provider"):
        _, provider = hetzner_provider()
    with Then("name is a string"):
        assert isinstance(provider.name, str)


@TestScenario
def server_to_provider_populates_root_disk_size(self):
    """_server_to_provider reads the bundled root disk (GB) from the server type."""
    from testflows.runners.providers.hetzner.utils import _server_to_provider

    with Given("a bound server whose type reports an 80 GB disk"):
        bound = _make_bound_server()
        bound.server_type.disk = 80
    with When("converting it to a ProviderServer"):
        ps = _server_to_provider(bound)
    with Then("root_disk_size reflects the type's disk"):
        assert ps.root_disk_size == 80, ps.root_disk_size


# ---------------------------------------------------------------------------
# list_runner_servers
# ---------------------------------------------------------------------------


@TestScenario
def list_runner_servers_label_selector(self):
    with Given("a Hetzner provider"):
        hclient, provider = hetzner_provider()
    with When("I call list_runner_servers"):
        hclient.servers.get_all.return_value = []
        provider.list_runner_servers()
    with Then("the owned selector and the legacy selectors are queried"):
        selectors = {
            c.kwargs["label_selector"]
            for c in hclient.servers.get_all.call_args_list
        }
        assert "github-runner=active" in selectors, selectors
        assert "github-hetzner-runner=active" in selectors, selectors


@TestScenario
def list_runner_servers_uses_isolation_tag(self):
    """Discovery filters by the controller's own id (isolation)."""
    with Given("a provider with a controller id"):
        hclient, provider = hetzner_provider()
        provider._runner_tag = "acme-infra"
    with When("I call list_runner_servers"):
        hclient.servers.get_all.return_value = []
        provider.list_runner_servers()
    with Then("the owned selector uses github-runner=<id>"):
        selectors = {
            c.kwargs["label_selector"]
            for c in hclient.servers.get_all.call_args_list
        }
        assert "github-runner=acme-infra" in selectors, selectors


@TestScenario
def list_runner_servers_claims_legacy_hetzner_server(self):
    """A live legacy server is adopted in place: its legacy keys are renamed to
    the neutral scheme and the discovery value becomes the controller id."""
    with Given("a provider with an id and a legacy Hetzner server"):
        hclient, provider = hetzner_provider()
        provider._runner_tag = "acme-infra"
        bound = _make_bound_server()
        bound.labels = {
            "github-hetzner-runner": "active",
            "github-hetzner-runner-ssh-key": "key-1",
            "github-hetzner-recycle-timestamp": "123",
            "github-hetzner-runner-label-0": "self-hosted",
        }

        def _get_all(label_selector=None, **kw):
            return [bound] if label_selector == "github-hetzner-runner=active" else []

        hclient.servers.get_all.side_effect = _get_all
    with When("I call list_runner_servers"):
        result = provider.list_runner_servers()
    with Then("the legacy server is returned, wrapping the bound server"):
        assert len(result) == 1 and result[0]._native is bound, result
    with And("it was retagged in place to the neutral scheme"):
        sent = bound.update.call_args[1]["labels"]
        assert sent["github-runner"] == "acme-infra", sent
        assert sent["github-runner-ssh-key"] == "key-1", sent
        assert sent["github-recycle-timestamp"] == "123", sent
        assert sent["github-runner-label-0"] == "self-hosted", sent
        for legacy_key in (
            "github-hetzner-runner",
            "github-hetzner-runner-ssh-key",
            "github-hetzner-recycle-timestamp",
            "github-hetzner-runner-label-0",
        ):
            assert legacy_key not in sent, (legacy_key, sent)


@TestScenario
def set_server_tags_deletes_on_none(self):
    with Given("a provider and a server carrying a legacy label"):
        _, provider = hetzner_provider()
        native = MagicMock()
        native.labels = {"github-hetzner-runner": "active", "keep": "v"}
        ps = _provider_server(
            labels=dict(native.labels), native=native
        )
    with When("I set the new label and delete the legacy one via None"):
        provider.set_server_tags(
            ps, {"github-runner": "acme-infra", "github-hetzner-runner": None}
        )
    with Then("native.update is called without the deleted key"):
        sent = native.update.call_args[1]["labels"]
        assert sent.get("github-runner") == "acme-infra", sent
        assert "github-hetzner-runner" not in sent, sent
        assert sent.get("keep") == "v", sent
    with And("the ProviderServer labels reflect it"):
        assert ps.labels.get("github-runner") == "acme-infra"
        assert "github-hetzner-runner" not in ps.labels


@TestScenario
def list_runner_servers_returns_provider_server(self):
    with Given("a Hetzner provider with one bound server owned by it"):
        hclient, provider = hetzner_provider()
        bound = _make_bound_server()

        def _get_all(label_selector=None, **kw):
            return [bound] if label_selector == "github-runner=active" else []

        hclient.servers.get_all.side_effect = _get_all
    with When("I call list_runner_servers"):
        result = provider.list_runner_servers()
    with Then("one ProviderServer is returned, wrapping the bound server"):
        assert len(result) == 1
        ps = result[0]
        assert isinstance(ps, ProviderServer)
        assert ps.name == bound.name
        assert ps._native is bound


@TestScenario
def list_runner_servers_maps_running(self):
    with Given("a Hetzner provider with a 'running' server"):
        hclient, provider = hetzner_provider()
        hclient.servers.get_all.return_value = [_make_bound_server(status="running")]
    with When("I call list_runner_servers"):
        result = provider.list_runner_servers()
    with Then("status maps to STATUS_RUNNING"):
        assert result[0].status == CloudProvider.STATUS_RUNNING


@TestScenario
def list_runner_servers_maps_off(self):
    with Given("a Hetzner provider with an 'off' server"):
        hclient, provider = hetzner_provider()
        hclient.servers.get_all.return_value = [_make_bound_server(status="off")]
    with When("I call list_runner_servers"):
        result = provider.list_runner_servers()
    with Then("status maps to STATUS_OFF"):
        assert result[0].status == CloudProvider.STATUS_OFF


# ---------------------------------------------------------------------------
# create_server
# ---------------------------------------------------------------------------


@TestScenario
def create_server_passes_args(self):
    with Given("a Hetzner provider"):
        hclient, provider = hetzner_provider()
        bound = _make_bound_server()
        mock_response = MagicMock()
        mock_response.server = bound
        hclient.servers.create.return_value = mock_response
    with When("I call create_server with full args"):
        server_type = MagicMock()
        location = MagicMock()
        image = MagicMock()
        ssh_keys = [MagicMock()]
        labels = {"github-runner": "active"}
        public_net = MagicMock()
        result = provider.create_server(
            name="test-server",
            server_type=server_type,
            location=location,
            image=image,
            ssh_keys=ssh_keys,
            labels=labels,
            volumes=[],
            public_net=public_net,
        )
    with Then("hclient.servers.create is called with those args"):
        hclient.servers.create.assert_called_once_with(
            name="test-server",
            server_type=server_type,
            location=location,
            image=image,
            ssh_keys=ssh_keys,
            labels=labels,
            volumes=[],
            automount=False,
            public_net=public_net,
        )
        assert isinstance(result, ProviderServer)
        assert result._native is bound


@TestScenario
def create_server_returns_provider_server(self):
    with Given("a Hetzner provider"):
        hclient, provider = hetzner_provider()
        bound = _make_bound_server(name="new-runner")
        mock_response = MagicMock()
        mock_response.server = bound
        hclient.servers.create.return_value = mock_response
    with When("I call create_server"):
        result = provider.create_server(
            name="new-runner",
            server_type=MagicMock(),
            location=MagicMock(),
            image=MagicMock(),
            ssh_keys=[MagicMock()],
            labels={},
        )
    with Then("a ProviderServer with the matching name is returned"):
        assert result.name == "new-runner"


# ---------------------------------------------------------------------------
# get_or_create_ssh_key
# ---------------------------------------------------------------------------


@TestScenario
def ssh_key_creates_when_missing(self):
    with Given("a Hetzner provider with no existing key"):
        hclient, provider = hetzner_provider()
        key_str = _fake_key_str()
        hclient.ssh_keys.get_by_fingerprint.return_value = None
        created_key = MagicMock()
        hclient.ssh_keys.create.return_value = created_key
    with When("I call get_or_create_ssh_key"):
        result = provider.get_or_create_ssh_key(public_key=key_str, is_file=False)
    with Then("hclient.ssh_keys.create is called and the new key returned"):
        hclient.ssh_keys.create.assert_called_once()
        assert result is created_key


@TestScenario
def ssh_key_returns_existing_when_present(self):
    with Given("a Hetzner provider where the key already exists"):
        hclient, provider = hetzner_provider()
        key_str = _fake_key_str()
        existing_key = MagicMock()
        hclient.ssh_keys.get_by_fingerprint.return_value = existing_key
    with When("I call get_or_create_ssh_key"):
        result = provider.get_or_create_ssh_key(public_key=key_str, is_file=False)
    with Then("hclient.ssh_keys.create is not called"):
        hclient.ssh_keys.create.assert_not_called()
        assert result is existing_key


@TestScenario
def ssh_key_reads_file_when_is_file_true(self):
    with Given("a Hetzner provider where the key already exists"):
        hclient, provider = hetzner_provider()
        key_str = _fake_key_str()
        existing_key = MagicMock()
        hclient.ssh_keys.get_by_fingerprint.return_value = existing_key
    with When("I call get_or_create_ssh_key with is_file=True"):
        with patch("builtins.open", MagicMock()) as mock_open:
            mock_open.return_value.__enter__ = MagicMock(
                return_value=MagicMock(read=MagicMock(return_value=key_str))
            )
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            result = provider.get_or_create_ssh_key(public_key="/path/to/key.pub", is_file=True)
    with Then("the file is read and the existing key returned"):
        mock_open.assert_called_once_with("/path/to/key.pub", "r", encoding="utf-8")
        assert result is existing_key


# ---------------------------------------------------------------------------
# get_server_tag / set_server_tags
# ---------------------------------------------------------------------------


@TestScenario
def get_server_tag_returns_value(self):
    with Given("a Hetzner provider and a server with a labelled tag"):
        _, provider = hetzner_provider()
        ps = _provider_server(labels={"my-key": "my-value"})
    with Then("get_server_tag returns the value"):
        assert provider.get_server_tag(ps, "my-key") == "my-value"


@TestScenario
def get_server_tag_none_for_missing(self):
    with Given("a Hetzner provider and a server with no labels"):
        _, provider = hetzner_provider()
        ps = _provider_server(labels={})
    with Then("get_server_tag returns None for any key"):
        assert provider.get_server_tag(ps, "missing") is None


@TestScenario
def set_server_tags_merges_and_updates(self):
    with Given("a Hetzner provider and a server with one existing label"):
        _, provider = hetzner_provider()
        native = MagicMock()
        native.labels = {"existing-key": "existing-val"}
        native.update = MagicMock()
        ps = _provider_server(labels={"existing-key": "existing-val"}, native=native)
    with When("I call set_server_tags with a new tag"):
        provider.set_server_tags(ps, {"new-key": "new-val"})
    with Then("the new tag is merged with the existing one and pushed to hcloud"):
        expected = {"existing-key": "existing-val", "new-key": "new-val"}
        native.update.assert_called_once_with(labels=expected)
        assert ps.labels == expected


@TestScenario
def set_server_tags_overwrites_existing(self):
    with Given("a Hetzner provider and a server with key 'k'='old'"):
        _, provider = hetzner_provider()
        native = MagicMock()
        native.labels = {"k": "old"}
        native.update = MagicMock()
        ps = _provider_server(labels={"k": "old"}, native=native)
    with When("I call set_server_tags with 'k'='new'"):
        provider.set_server_tags(ps, {"k": "new"})
    with Then("the value is overwritten"):
        assert ps.labels["k"] == "new"


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


@TestScenario
def power_off_calls_native(self):
    with Given("a Hetzner provider and a server"):
        _, provider = hetzner_provider()
        native = MagicMock()
    with When("I call power_off_server"):
        provider.power_off_server(_provider_server(native=native))
    with Then("the native server's power_off is called"):
        native.power_off.assert_called_once()


@TestScenario
def delete_server_calls_native(self):
    with Given("a Hetzner provider and a server"):
        _, provider = hetzner_provider()
        native = MagicMock()
    with When("I call delete_server"):
        provider.delete_server(_provider_server(native=native))
    with Then("the native server's delete is called"):
        native.delete.assert_called_once()


@TestScenario
def power_on_calls_native(self):
    with Given("a Hetzner provider and a server"):
        _, provider = hetzner_provider()
        native = MagicMock()
        action = MagicMock()
        native.power_on.return_value = action
    with When("I call power_on_server"):
        provider.power_on_server(_provider_server(native=native))
    with Then("the native server's power_on is called and waited on"):
        native.power_on.assert_called_once()
        action.wait_until_finished.assert_called_once_with(max_retries=300)


@TestScenario
def power_on_propagates_timeout(self):
    with Given("a Hetzner provider and a server"):
        _, provider = hetzner_provider()
        native = MagicMock()
        action = MagicMock()
        native.power_on.return_value = action
    with When("I call power_on_server with timeout=42"):
        provider.power_on_server(_provider_server(native=native), timeout=42)
    with Then("the timeout is propagated as max_retries"):
        action.wait_until_finished.assert_called_once_with(max_retries=42)


@TestScenario
def setup_script_name_defaults_to_setup_sh(self):
    with Given("a Hetzner provider"):
        _, provider = hetzner_provider()
    with Then("its setup-step script defaults to setup.sh"):
        assert provider.setup_script_name([]) == "setup.sh"
    with And("a setup-<name> label overrides it"):
        assert provider.setup_script_name(["setup-custom"]) == "custom.sh"
    with And("a recycle-<name> label does NOT affect the setup-step for cloud"):
        assert provider.setup_script_name(["recycle-clean"]) == "setup.sh"


# ---------------------------------------------------------------------------
# expand_location_label (pure function — no fixtures needed)
# ---------------------------------------------------------------------------


def _expand(name):
    return HetznerCloudProvider.expand_location_label(None, name)


@TestScenario
def expand_simple_label_single_element(self):
    assert _expand("nbg1") == ["nbg1"]


@TestScenario
def expand_composite_three_parts(self):
    assert _expand("hel1-fsn1-nbg1") == ["hel1", "fsn1", "nbg1"]


@TestScenario
def expand_composite_two_parts(self):
    assert _expand("hel1-fsn1") == ["hel1", "fsn1"]


@TestScenario
def expand_aws_az_not_expanded(self):
    """AWS AZ names like us-east-1a must not be treated as composite."""
    assert _expand("us-east-1a") == ["us-east-1a"]


@TestScenario
def expand_aws_region_not_expanded(self):
    assert _expand("eu-west-1") == ["eu-west-1"]


@TestScenario
def expand_without_dash(self):
    assert _expand("nbg1") == ["nbg1"]


@TestScenario
def expand_digits_only_part_not_dc_code(self):
    """A segment like '1a' does not match the pure-digit suffix pattern."""
    assert _expand("us-east-1a") == ["us-east-1a"]


# ---------------------------------------------------------------------------
# Feature entry point
# ---------------------------------------------------------------------------


@TestFeature
@Name("hetzner provider")
def feature(self):
    """HetznerCloudProvider unit tests."""
    for scenario in loads(current_module(), Scenario):
        scenario()
