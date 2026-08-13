"""Cloud deploy generalized to the CloudProvider abstraction (AWS + Hetzner).

Verifies deploy provisioning routes through CloudProvider (not hcloud directly),
the deploy provider is selected from config.cloud.provider, non-Hetzner deploy
specs stay provider-native strings, and the SSH user is generalized (no hardcoded
root@/ubuntu that would break on AWS).
"""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from testflows.core import *

from testflows.github.runners.config.parse import parse_config
from testflows.github.runners.cloud_provider import CloudProvider, ProviderServer
import testflows.github.runners.cloud as cloud


_MINIMAL_AWS = """
config:
  github_token: t
  github_repository: o/r
  ssh_key: {ssh_key}
  providers:
    aws:
      access_key_id: AK
      secret_access_key: SK
      security_group: sg-1
  cloud:
    provider: aws
    server_name: ctl
    deploy:
      server_type: t3.medium
      image: "resolve:ssm:/aws/ubuntu"
      location: us-east-1a
"""


def _write(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    f.write(text)
    f.close()
    return f.name


def _key_file():
    f = tempfile.NamedTemporaryFile("w", suffix=".pub", delete=False)
    f.write("ssh-ed25519 AAAAC3NzaC1lZDI1 deploy@key\n")
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@TestScenario
def as_service_user_branches_on_login_user(self):
    root = ProviderServer(id="s", name="s", status="running", public_ipv4="1.2.3.4",
                          private_ipv4=None, labels={}, server_type="", location="",
                          created=None, ssh_user="root")
    ubuntu = ProviderServer(id="s", name="s", status="running", public_ipv4="1.2.3.4",
                            private_ipv4=None, labels={}, server_type="", location="",
                            created=None, ssh_user="ubuntu")
    with Then("root login drops into the service user via su"):
        assert cloud.as_service_user(root, "x") == "\"su - ubuntu -c 'x'\""
    with And("ubuntu login runs directly (no su self-switch)"):
        assert cloud.as_service_user(ubuntu, "x") == "'x'"


@TestScenario
def sudo_if_needed_only_for_non_root(self):
    root = ProviderServer(id="s", name="s", status="running", public_ipv4="1.2.3.4",
                          private_ipv4=None, labels={}, server_type="", location="",
                          created=None, ssh_user="root")
    ubuntu = ProviderServer(id="s", name="s", status="running", public_ipv4="1.2.3.4",
                            private_ipv4=None, labels={}, server_type="", location="",
                            created=None, ssh_user="ubuntu")
    assert cloud.sudo_if_needed(root, "chown x") == "chown x"
    assert cloud.sudo_if_needed(ubuntu, "chown x") == "sudo chown x"


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


@TestScenario
def aws_cloud_provider_keeps_specs_as_strings(self):
    key = _key_file()
    path = _write(_MINIMAL_AWS.format(ssh_key=key))
    try:
        with When("an AWS cloud.provider config is parsed"):
            cfg = parse_config(path)
        with Then("provider is aws and deploy specs stay provider-native strings"):
            assert cfg.cloud.provider == "aws", cfg.cloud.provider
            assert cfg.cloud.deploy.server_type == "t3.medium"
            assert cfg.cloud.deploy.image == "resolve:ssm:/aws/ubuntu"
            assert cfg.cloud.deploy.location == "us-east-1a"
    finally:
        os.unlink(path)
        os.unlink(key)


@TestScenario
def invalid_cloud_provider_is_rejected(self):
    key = _key_file()
    path = _write(_MINIMAL_AWS.format(ssh_key=key).replace("provider: aws", "provider: dedicated_static"))
    try:
        with Then("a non-hostable cloud.provider is rejected with guidance"):
            try:
                parse_config(path)
                assert False, "expected AssertionError for dedicated_static cloud.provider"
            except AssertionError as exc:
                assert "cloud.provider" in str(exc), exc
    finally:
        os.unlink(path)
        os.unlink(key)


# ---------------------------------------------------------------------------
# Provider selection + host mode
# ---------------------------------------------------------------------------


def _mock_provider(name="aws", ssh_user="ubuntu"):
    p = MagicMock()
    p.name = name
    p.ssh_user = ssh_user
    return p


@TestScenario
def deploy_provider_selects_by_name(self):
    aws = _mock_provider("aws")
    hetzner = _mock_provider("hetzner", ssh_user="root")
    config = SimpleNamespace(cloud=SimpleNamespace(provider="aws"))
    with patch.object(cloud, "provider_factory", return_value=[hetzner, aws]):
        with Then("the provider matching cloud.provider is returned"):
            assert cloud.deploy_provider(config) is aws
    with And("an unconfigured provider name raises"):
        config2 = SimpleNamespace(cloud=SimpleNamespace(provider="scaleway"))
        with patch.object(cloud, "provider_factory", return_value=[hetzner, aws]):
            try:
                cloud.deploy_provider(config2)
                assert False, "expected ValueError for unconfigured provider"
            except ValueError:
                pass


@TestScenario
def get_server_host_mode_uses_provider_ssh_user(self):
    aws = _mock_provider("aws", ssh_user="ubuntu")
    config = SimpleNamespace(
        cloud=SimpleNamespace(provider="aws", server_name="ctl", host="203.0.113.9", ssh_user=None)
    )
    with When("host mode is used with a provider passed"):
        server = cloud.get_server(config, provider=aws)
    with Then("a ProviderServer is built with the host IP and the provider ssh_user"):
        assert isinstance(server, ProviderServer)
        assert server.public_ipv4 == "203.0.113.9"
        assert server.ssh_user == "ubuntu"
        aws.get_server.assert_not_called()


@TestScenario
def get_server_host_mode_needs_no_provider(self):
    """--host with no provider configured must not require one (dashboard/log/etc).

    Regression guard: get_server used to resolve a provider just for ssh_user,
    so `cloud --host X dashboard` failed with 'provider not configured'.
    """
    config = SimpleNamespace(
        cloud=SimpleNamespace(provider="hetzner", server_name="ctl", host="builders", ssh_user=None)
    )
    with When("host mode is used and no provider is configured"), patch.object(
        cloud, "provider_factory", return_value=[]
    ):
        server = cloud.get_server(config)
    with Then("the host resolves with ssh_user left to ssh (None), no error"):
        assert server.public_ipv4 == "builders"
        assert server.ssh_user is None


@TestScenario
def get_server_host_mode_explicit_user_wins(self):
    """An explicit config.cloud.ssh_user overrides any provider default."""
    aws = _mock_provider("aws", ssh_user="ubuntu")
    config = SimpleNamespace(
        cloud=SimpleNamespace(provider="aws", server_name="ctl", host="1.2.3.4", ssh_user="admin")
    )
    with When("host mode is used with an explicit ssh_user"):
        server = cloud.get_server(config, provider=aws)
    with Then("the explicit user wins over the provider's"):
        assert server.ssh_user == "admin"


@TestScenario
def ssh_command_omits_user_when_none(self):
    """A direct host with no ssh_user lets ssh resolve the login (alias case)."""
    from testflows.github.runners.server import ssh_command

    alias = ProviderServer(id="s", name="s", status="running", public_ipv4="builders",
                           private_ipv4=None, labels={}, server_type="", location="",
                           created=None, ssh_user=None)
    rooted = ProviderServer(id="s", name="s", status="running", public_ipv4="1.2.3.4",
                            private_ipv4=None, labels={}, server_type="", location="",
                            created=None, ssh_user="root")
    with Then("no user@ is prepended for an alias host"):
        cmd = ssh_command(alias)
        assert cmd.endswith(" builders"), cmd
        assert "@" not in cmd, cmd
    with And("a user is prepended when set"):
        assert ssh_command(rooted).endswith(" root@1.2.3.4"), ssh_command(rooted)


# ---------------------------------------------------------------------------
# deploy() routes through the abstraction
# ---------------------------------------------------------------------------


@TestScenario
def deploy_routes_through_provider_and_uses_ssh_user(self):
    key = _key_file()
    path = _write(_MINIMAL_AWS.format(ssh_key=key))
    try:
        cfg = parse_config(path)
        cfg.scripts = None
        provider = _mock_provider("aws", ssh_user="ubuntu")
        provider.get_or_create_ssh_key.return_value = SimpleNamespace(name="k")
        provider.get_server.return_value = None  # force path: nothing to delete
        provider.get_image.side_effect = lambda s: f"img:{s}"
        provider.get_server_type.side_effect = lambda s: f"type:{s}"
        provider.get_location.side_effect = lambda s: f"loc:{s}"
        provider.build_server_labels.return_value = {}
        created = ProviderServer(
            id="i-1", name="ctl", status="running", public_ipv4="203.0.113.9",
            private_ipv4=None, labels={}, server_type="t3.medium",
            location="us-east-1a", created=None, ssh_user="ubuntu",
        )
        provider.create_server.return_value = created

        args = SimpleNamespace(version=None, force=True)
        with patch.object(cloud, "provider_factory", return_value=[provider]), \
             patch.object(cloud, "wait_ssh"), \
             patch.object(cloud, "ssh") as ssh_mock, \
             patch.object(cloud, "scp") as scp_mock, \
             patch.object(cloud, "install") as install_mock:
            with When("deploy runs against the AWS provider"):
                cloud.deploy(args, cfg)

        with Then("provisioning goes through the CloudProvider abstraction"):
            provider.get_or_create_ssh_key.assert_any_call(cfg.ssh_key, is_file=True)
            provider.get_image.assert_called_with("resolve:ssm:/aws/ubuntu")
            provider.get_server_type.assert_called_with("t3.medium")
            provider.create_server.assert_called_once()
            ckw = provider.create_server.call_args.kwargs
            assert ckw["image"] == "img:resolve:ssm:/aws/ubuntu", ckw
            assert ckw["server_type"] == "type:t3.medium", ckw
        with And("the created ProviderServer is handed to install"):
            assert install_mock.call_args.kwargs["server"] is created
        with And("scp uploads use the provider ssh_user, never a hardcoded root@"):
            dests = [c.kwargs.get("destination", "") for c in scp_mock.call_args_list]
            assert dests, "expected at least one scp upload"
            assert all(d.startswith("ubuntu@") for d in dests), dests
        with And("setup.sh is run with sudo since login user is not root"):
            setup_calls = [
                c.args[1] for c in ssh_mock.call_args_list
                if len(c.args) > 1 and "bash -s" in str(c.args[1])
            ]
            assert setup_calls and all("sudo bash -s" in s for s in setup_calls), setup_calls
    finally:
        os.unlink(path)
        os.unlink(key)


@TestFeature
@Name("cloud deploy")
def feature(self):
    for scenario in loads(current_module(), Scenario):
        scenario()
