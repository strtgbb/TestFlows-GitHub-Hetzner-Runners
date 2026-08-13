"""Tests for the Hetzner provider config-section validation:
providers.hetzner.* parsing in isolation, plus the full
YAML -> parse_config() -> cfg.providers.hetzner integration path.

No real Hetzner API calls are made; this module is pure config logic.
"""
import os
import tempfile

from testflows.core import *

from testflows.github.runners.config.parse import parse_config
from testflows.github.runners.config.config import Config, hetzner_provider, provider_list
from testflows.github.runners.config.factory import provider_factory


_MINIMAL_BASE = """
config:
  github_token: token
  github_repository: owner/repo
  ssh_key: /tmp/key
"""


# ---------------------------------------------------------------------------
# parse_config_section: direct unit tests of the moved Hetzner validation
# ---------------------------------------------------------------------------


@TestScenario
def hetzner_parse_section_validates(self):
    """parse_config_section() coerces a valid section into hetzner_provider
    and rejects an invalid end_of_life value, mirroring parse_config()'s
    validation of providers.hetzner.end_of_life.
    """
    from testflows.github.runners.providers.hetzner import config as hz_config

    with Then("a valid section coerces into the dataclass"):
        cfg = hz_config.parse_config_section({"token": "t"})
        assert cfg.token == "t", cfg

    with And("an invalid end_of_life is rejected with an assertion"):
        try:
            hz_config.parse_config_section({"token": "t", "end_of_life": 999})
            assert False, "expected rejection"
        except AssertionError as e:
            assert "end_of_life" in str(e), str(e)


# ---------------------------------------------------------------------------
# parse_config: full YAML -> providers.hetzner integration
# ---------------------------------------------------------------------------


@TestScenario
def config_parses_hetzner_recycle_with_rebuild(self):
    text = _MINIMAL_BASE + """
  providers:
    hetzner:
      token: token
      recycle_with_rebuild: true
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        with When("I parse the provider-specific recycling setting"):
            cfg = parse_config(path)
        with Then("the value is stored under the Hetzner provider"):
            assert cfg.providers.hetzner.recycle_with_rebuild is True
    finally:
        os.unlink(path)


@TestScenario
def factory_passes_hetzner_recycle_with_rebuild(self):
    cfg = Config(
        providers=provider_list(
            hetzner=hetzner_provider(token="token", recycle_with_rebuild=True)
        )
    )
    with When("I construct providers from the config"):
        provider = provider_factory(cfg)[0]
    with Then("the Hetzner provider receives the recycling mode"):
        assert provider._recycle_with_rebuild is True


@TestScenario
def config_parses_hetzner_defaults(self):
    """providers.hetzner.defaults is parsed into the Hetzner provider config."""
    text = _MINIMAL_BASE + """
  providers:
    hetzner:
      token: token
      defaults:
        image: "x86:system:ubuntu-20.04"
        server_type: cpx31
        location: fsn1
        volume_size: 50
        volume_location: hel1
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        with When("I parse the Hetzner defaults block"):
            cfg = parse_config(path)
        with Then("the values are stored under providers.hetzner.defaults"):
            d = cfg.providers.hetzner.defaults
            assert d.image == "x86:system:ubuntu-20.04", d.image
            assert d.server_type == "cpx31", d.server_type
            assert d.location == "fsn1", d.location
            assert d.volume_size == 50, d.volume_size
            assert d.volume_location == "hel1", d.volume_location
    finally:
        os.unlink(path)


@TestScenario
def factory_passes_hetzner_defaults(self):
    """providers.hetzner.defaults reach the HetznerCloudProvider default_* specs."""
    cfg = Config(
        providers=provider_list(
            hetzner=hetzner_provider(token="token")
        )
    )
    cfg.providers.hetzner.defaults.image = "x86:system:ubuntu-20.04"
    cfg.providers.hetzner.defaults.server_type = "cpx31"
    cfg.providers.hetzner.defaults.location = "fsn1"
    cfg.providers.hetzner.defaults.volume_size = 50
    cfg.providers.hetzner.defaults.volume_location = "hel1"
    with When("I construct providers from the config"):
        provider = provider_factory(cfg)[0]
    with Then("the Hetzner provider exposes the configured defaults as specs"):
        assert provider.default_image == "x86:system:ubuntu-20.04"
        assert provider.default_server_type == "cpx31"
        assert provider.default_location == "fsn1"
        assert provider.default_volume_size == 50
        assert provider.default_volume_location == "hel1"


# ---------------------------------------------------------------------------
# Feature entry point
# ---------------------------------------------------------------------------


@TestFeature
@Name("hetzner config")
def feature(self):
    """Hetzner provider config-section parsing/validation tests."""
    for scenario in loads(current_module(), Scenario):
        scenario()
