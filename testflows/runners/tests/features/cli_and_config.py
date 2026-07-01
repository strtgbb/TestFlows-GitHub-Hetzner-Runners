"""CLI smoke tests and config-layer regression tests.

Covers:
- CLI --help exits 0 and produces output (smoke test)
- provider_type() accepts valid providers (hetzner/aws/scaleway) and rejects
  not-yet-implemented ones (azure/gcp)
- Config parser rejects azure/gcp with a clear message
- schema.json lists hetzner, aws and scaleway under providers.properties
"""
import json
import os
import subprocess
import sys
from argparse import ArgumentTypeError

from testflows.core import *

from testflows.runners.args import provider_type
from testflows.runners.config.parse import parse_config

# Repo root so the CLI subprocess can find the package without an install.
_REPO_ROOT = os.path.abspath(os.path.join(current_dir(), "..", "..", "..", ".."))
_CLI_SCRIPT = os.path.join(_REPO_ROOT, "testflows", "runners", "bin", "tfs-runners")
_SCHEMA_PATH = os.path.join(_REPO_ROOT, "testflows", "runners", "config", "schema.json")


def _run_help():
    env = os.environ.copy()
    env["PYTHONPATH"] = _REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, _CLI_SCRIPT, "--help"],
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# 1. CLI smoke test
# ---------------------------------------------------------------------------


@TestScenario
def cli_help_exits_zero(self):
    with When("I run `tfs-runners --help`"):
        result = _run_help()
    with Then("it exits with code 0"):
        assert result.returncode == 0, f"--help exited {result.returncode}:\n{result.stderr}"


@TestScenario
def cli_help_mentions_known_providers(self):
    with When("I run `tfs-runners --help`"):
        result = _run_help()
    with Then("the output mentions hetzner, aws and scaleway"):
        output = result.stdout + result.stderr
        assert "hetzner" in output
        assert "aws" in output
        assert "scaleway" in output


@TestScenario
def cli_help_does_not_mention_removed_providers(self):
    with When("I run `tfs-runners --help`"):
        result = _run_help()
    with Then("the output does not mention azure / gcp"):
        output = result.stdout + result.stderr
        for removed in ("azure", "gcp"):
            assert removed not in output, f"removed provider '{removed}' still appears in --help"


# ---------------------------------------------------------------------------
# 2. provider_type() whitelist
# ---------------------------------------------------------------------------


@TestScenario
def provider_type_accepts_valid(self):
    for value in ("hetzner", "aws", "scaleway", "hetzner,aws", "aws,scaleway"):
        with When(f"I parse provider_type({value!r})"):
            result = provider_type(value)
        with Then("the result is a list of known providers"):
            assert isinstance(result, list)
            assert all(
                p in {"hetzner", "aws", "scaleway"} for p in result
            ), f"unexpected for {value}: {result}"


@TestScenario
def provider_type_rejects_removed_providers(self):
    for removed in ("azure", "gcp"):
        with When(f"I parse provider_type({removed!r})"):
            try:
                provider_type(removed)
                raised_msg = None
            except ArgumentTypeError as e:
                raised_msg = str(e)
        with Then(f"ArgumentTypeError is raised and mentions {removed!r}"):
            assert raised_msg is not None
            assert removed in raised_msg


@TestScenario
def provider_type_error_message_lists_valid_providers(self):
    with When("I parse provider_type('azure')"):
        try:
            provider_type("azure")
            msg = None
        except ArgumentTypeError as e:
            msg = str(e)
    with Then("the error message lists hetzner and aws"):
        assert msg is not None
        assert "hetzner" in msg
        assert "aws" in msg


@TestScenario
def provider_type_deduplicates(self):
    with When("I parse provider_type('hetzner,hetzner')"):
        result = provider_type("hetzner,hetzner")
    with Then("the result contains hetzner only once"):
        assert result.count("hetzner") == 1


# ---------------------------------------------------------------------------
# 3. hetzner_token is not auto-discovered from the environment
# ---------------------------------------------------------------------------


@TestScenario
def hetzner_token_not_read_from_env(self):
    """An ambient HETZNER_TOKEN must not populate config.hetzner_token.

    Hetzner must be configured explicitly (--hetzner-token, config file
    hetzner_token, or providers.hetzner.token); a stray env var must not
    silently create a Hetzner provider.
    """
    from testflows.runners.config.config import Config

    saved = os.environ.get("HETZNER_TOKEN")
    os.environ["HETZNER_TOKEN"] = "ambient-should-be-ignored"
    try:
        with When("I build a Config with HETZNER_TOKEN set in the environment"):
            cfg = Config(github_token="t", github_repository="o/r")
        with Then("config.hetzner_token is not populated from the env"):
            assert cfg.hetzner_token is None, cfg.hetzner_token
    finally:
        if saved is None:
            os.environ.pop("HETZNER_TOKEN", None)
        else:
            os.environ["HETZNER_TOKEN"] = saved


# ---------------------------------------------------------------------------
# 4. Config parser rejects removed providers
# ---------------------------------------------------------------------------


_MINIMAL_BASE = """
config:
  github_token: token
  github_repository: owner/repo
  ssh_key: /tmp/key
"""


def _write_minimal_with_provider(provider_name):
    import tempfile
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(_MINIMAL_BASE + f"  providers:\n    {provider_name}:\n      dummy: value\n")
    f.close()
    return f.name


@TestScenario
def config_rejects_removed_providers(self):
    for provider_name in ("azure", "gcp"):
        with Given(f"a config referencing the removed provider {provider_name!r}"):
            cfg_file = _write_minimal_with_provider(provider_name)
        try:
            with When("I call parse_config"):
                try:
                    parse_config(cfg_file)
                    raised = None
                except (AssertionError, SystemExit) as e:
                    raised = e
            with Then("a clear rejection error is raised"):
                assert raised is not None, f"expected rejection for {provider_name}"
                msg = str(raised).lower()
                assert "not yet implemented" in msg or provider_name in msg, (
                    f"Expected clear rejection message, got: {raised!r}"
                )
        finally:
            os.unlink(cfg_file)


# ---------------------------------------------------------------------------
# 4. Schema regression: only hetzner and aws under providers
# ---------------------------------------------------------------------------


def _providers_properties(schema):
    """Navigate to schema.properties.config.properties.providers.properties."""
    return (
        schema
        .get("properties", {})
        .get("config", {})
        .get("properties", {})
        .get("providers", {})
        .get("properties", {})
    )


@TestScenario
def schema_implemented_providers_defined(self):
    with Given("the schema.json file"):
        with open(_SCHEMA_PATH) as f:
            schema = json.load(f)
    with Then("hetzner, aws and scaleway are defined under providers"):
        props = _providers_properties(schema)
        assert set(props.keys()) == {"hetzner", "aws", "scaleway"}, (
            f"Unexpected providers in schema: {set(props.keys())}"
        )


@TestScenario
def schema_removed_provider_absent(self):
    with Given("the schema.json file"):
        with open(_SCHEMA_PATH) as f:
            schema = json.load(f)
    with Then("azure / gcp are absent from providers"):
        props = _providers_properties(schema)
        for removed in ("azure", "gcp"):
            assert removed not in props, f"removed provider '{removed}' still in schema"


# ---------------------------------------------------------------------------
# Feature entry point
# ---------------------------------------------------------------------------


@TestFeature
@Name("cli and config")
def feature(self):
    """CLI smoke + provider whitelist + schema regression tests."""
    for scenario in loads(current_module(), Scenario):
        scenario()
