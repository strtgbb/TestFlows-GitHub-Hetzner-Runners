"""Tests for the Scaleway provider: type translation, args validation,
config parsing/propagation, and the canonical-type runner-name round-trip.

No real Scaleway API calls are made; the optional ``scaleway`` SDK is faked via
``mock_scaleway_sdk`` for the factory-construction scenario.  Everything else is
pure logic and needs no SDK.
"""
from argparse import ArgumentTypeError

from testflows.core import *

from testflows.runners.config.parse import parse_config
from testflows.runners.config.factory import provider_factory
from testflows.runners.config.config import (
    Config,
    provider_list,
    scaleway_provider as scaleway_provider_config,
)
from types import SimpleNamespace

from testflows.runners.cloud_provider import ProviderServer, ProviderServerType
from testflows.runners.errors import ImageError, ImageSpecFormatError
from testflows.runners.providers.scaleway import utils, args as scw_args
from testflows.runners.scale_up import get_server_types, get_runner_server_type
from testflows.runners.server import get_runner_server_name
from testflows.runners.constants import runner_name_prefix
from testflows.runners.tests.steps.config import write_config
from testflows.runners.tests.steps.scaleway import mock_scaleway_sdk, scaleway_provider


class _FakeImage:
    """Minimal stand-in for a Scaleway Image (private or local marketplace)."""

    def __init__(self, id, name="", arch="x86_64"):
        self.id = id
        self.name = name
        self.arch = arch


# Native Scaleway types (dash-form) <-> canonical (dot-form) used across tests.
_TYPES = [
    ("DEV1-S", "dev1.s"),
    ("GP1-XS", "gp1.xs"),
    ("PRO2-XXS", "pro2.xxs"),
    ("POP2-2C-8G", "pop2.2c.8g"),  # multi-dash
    ("BASIC2-A8C-16G", "basic2.a8c.16g"),  # multi-dash
]


# ---------------------------------------------------------------------------
# Type translation: the dot <-> dash bijection
# ---------------------------------------------------------------------------


@TestScenario
def native_to_canonical(self):
    """Native dash-form translates to dash-free canonical dot-form."""
    for native, canonical in _TYPES:
        assert utils.canonical_type(native) == canonical, native
        assert "-" not in utils.canonical_type(native), native


@TestScenario
def canonical_to_native_roundtrip(self):
    """canonical -> native -> canonical is a clean round-trip (incl. multi-dash)."""
    for native, canonical in _TYPES:
        assert utils.native_type(canonical) == native, canonical
        assert utils.canonical_type(utils.native_type(canonical)) == canonical


# ---------------------------------------------------------------------------
# Scaleway list[str] tags <-> dict labels
# ---------------------------------------------------------------------------


@TestScenario
def tags_dict_roundtrip(self):
    """A label dict round-trips through Scaleway's list[str] tag format."""
    labels = {
        "github-runner": "active",
        "github-runner-label-0": "self-hosted",
        "bare": "",
    }
    tags = utils.dict_to_tags(labels)
    assert "bare" in tags and "bare=" not in tags, tags
    assert utils.tags_to_dict(tags) == labels, tags


# ---------------------------------------------------------------------------
# args validator: the CLI-side guard for the '-' footgun
# ---------------------------------------------------------------------------


@TestScenario
def args_server_type_rejects_dash(self):
    """Dash-form server type is rejected with a message pointing at the dot-form."""
    try:
        scw_args.server_type("DEV1-S")
        assert False, "expected ArgumentTypeError for dash-form type"
    except ArgumentTypeError as exc:
        assert "dev1.s" in str(exc), str(exc)


@TestScenario
def args_server_type_accepts_dot(self):
    """Dot-form server types (incl. multi-dot) are accepted and lower-cased."""
    assert scw_args.server_type("dev1.s") == "dev1.s"
    assert scw_args.server_type("GP1.XS") == "gp1.xs"
    assert scw_args.server_type("pop2.2c.8g") == "pop2.2c.8g"


@TestScenario
def args_zone_validation(self):
    """Zone validator accepts valid zones and rejects malformed ones."""
    assert scw_args.location_type("fr-par-1") == "fr-par-1"
    assert scw_args.location_type("nl-ams-2") == "nl-ams-2"
    try:
        scw_args.location_type("paris")
        assert False, "expected ArgumentTypeError for invalid zone"
    except ArgumentTypeError:
        pass


# ---------------------------------------------------------------------------
# The core concern: canonical types survive the runner-name encoding
# ---------------------------------------------------------------------------


@TestScenario
def get_server_types_accepts_dot_skips_dash(self):
    """type-<dot> labels are honoured; the dash-form is skipped (composite-label rule)."""
    with When("a job carries a dot-form type label"):
        types = get_server_types(["self-hosted", "type-dev1.s"], default="dev1.m")
    with Then("the dot-form type is selected"):
        assert types == ["dev1.s"], types
    with When("a job carries a dash-form type label"):
        types = get_server_types(["type-dev1-s"], default="dev1.m")
    with Then("the dash-form is skipped and the default is used"):
        assert types == ["dev1.m"], types


@TestScenario
def runner_name_roundtrip_for_dot_types(self):
    """A canonical dot-type embedded in a runner name decodes back intact.

    Covers both ``get_runner_server_type`` (remainder capture) and
    ``get_runner_server_name`` (the ``[:5]`` truncation), which only stay
    correct because the canonical form is dash-free.
    """
    for _native, canonical in _TYPES:
        name = f"{runner_name_prefix}run1-0-{canonical}"
        with Then(f"type decodes from name for {canonical}"):
            assert get_runner_server_type(name) == canonical, name
        with And(f"server name reconstructs intact for {canonical}"):
            assert get_runner_server_name(name) == name, name


# ---------------------------------------------------------------------------
# parse_config: propagation and dash rejection
# ---------------------------------------------------------------------------


@TestScenario
def parse_propagates_credentials_and_defaults(self):
    """Scaleway creds and defaults in YAML reach cfg.providers.scaleway."""
    with Given("a config file"):
        path = write_config(yaml_text="""\
            ssh_key: /dev/null
            providers:
              scaleway:
                access_key: SCWTEST
                secret_key: s3cr3t
                project_id: 11111111-1111-1111-1111-111111111111
                defaults:
                  server_type: dev1.m
                  location: fr-par-1
                  image: ubuntu_jammy
        """)
    with When("I parse the config"):
        cfg = parse_config(path)
    with Then("credentials and defaults match"):
        scw = cfg.providers.scaleway
        assert scw.access_key == "SCWTEST", scw
        assert scw.secret_key == "s3cr3t", scw
        assert scw.project_id == "11111111-1111-1111-1111-111111111111", scw
        assert scw.defaults.server_type == "dev1.m", scw
        assert scw.defaults.location == "fr-par-1", scw
        assert scw.defaults.image == "ubuntu_jammy", scw


@TestScenario
def parse_rejects_dash_server_type(self):
    """A dash-form default server_type in config is rejected with guidance."""
    with Given("a config file with a dash-form server_type"):
        path = write_config(yaml_text="""\
            ssh_key: /dev/null
            providers:
              scaleway:
                access_key: AK
                secret_key: SK
                project_id: pid
                defaults:
                  server_type: DEV1-M
        """)
    with Then("parsing raises with a dot-form hint"):
        try:
            parse_config(path)
            assert False, "expected dash-form server_type to be rejected"
        except AssertionError as exc:
            assert "dot-form" in str(exc), str(exc)


# ---------------------------------------------------------------------------
# factory: YAML -> provider_factory -> ScalewayCloudProvider (SDK faked)
# ---------------------------------------------------------------------------


@TestScenario
def factory_builds_scaleway_provider(self):
    """provider_factory constructs a ScalewayCloudProvider with config values."""
    with Given("a faked scaleway SDK"):
        mock_scaleway_sdk()
    with And("a config file"):
        path = write_config(yaml_text="""\
            ssh_key: /dev/null
            providers:
              scaleway:
                access_key: AK
                secret_key: SK
                project_id: proj-123
                defaults:
                  location: nl-ams-1
                  image: ubuntu_jammy
        """)
    with When("I parse and build providers"):
        cfg = parse_config(path)
        providers = provider_factory(cfg)
    with Then("exactly one scaleway provider is returned with config applied"):
        scw = [p for p in providers if p.name == "scaleway"]
        assert len(scw) == 1, [p.name for p in providers]
        provider = scw[0]
        assert provider._project_id == "proj-123"
        assert provider._zone == "nl-ams-1"
        assert provider._default_image == "ubuntu_jammy"
        assert provider.supports_recycling is False


@TestScenario
def ambient_hetzner_token_does_not_override_scaleway(self):
    """An ambient hetzner_token must not auto-wire Hetzner when scaleway is set.

    Reproduces the case where HETZNER_TOKEN is present in the environment but the
    user has explicitly configured providers.scaleway: the factory must build
    only the scaleway provider, not a surprise Hetzner one.
    """
    with Given("a faked scaleway SDK"):
        mock_scaleway_sdk()
    with And("a config with explicit scaleway and an ambient hetzner_token"):
        cfg = Config(github_token="t", github_repository="o/r")
        cfg.hetzner_token = "ambient-hetzner-token"
        cfg.providers = provider_list(
            scaleway=scaleway_provider_config(
                access_key="AK", secret_key="SK", project_id="pid"
            )
        )
    with When("I build providers"):
        providers = provider_factory(cfg)
    with Then("only the scaleway provider is constructed"):
        assert [p.name for p in providers] == ["scaleway"], [p.name for p in providers]


# ---------------------------------------------------------------------------
# get_server_arch: authoritative SDK arch, with a name fallback
# ---------------------------------------------------------------------------


@TestScenario
def get_server_arch_uses_sdk_arch(self):
    """Arch comes from the SDK ServerType.arch, not a name guess.

    Reproduces the ARM instance whose name ('basic2.a8c.16g') does not look ARM
    but whose SDK arch is arm64 — it must resolve to arm64, not x64.
    """
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with Then("arm/arm64 SDK arch -> arm64 and x86_64 -> x64, regardless of name"):
        for sdk_arch, expected in [("arm64", "arm64"), ("arm", "arm64"), ("x86_64", "x64")]:
            st = ProviderServerType(name="basic2.a8c.16g", _native=SimpleNamespace(arch=sdk_arch))
            assert provider.get_server_arch(st) == expected, (sdk_arch, expected)


@TestScenario
def get_server_arch_defaults_x64_without_sdk_type(self):
    """A bare ProviderServerType (no SDK object) defaults to x64.

    Arch is authoritative from the SDK ServerType; the name is never parsed for
    architecture (Scaleway type names do not reliably encode it).
    """
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with Then("arch defaults to x64 when there is no SDK ServerType"):
        assert provider.get_server_arch(ProviderServerType(name="dev1.s")) == "x64"
        assert provider.get_server_arch(ProviderServerType(name="basic2.a8c.16g")) == "x64"


@TestScenario
def stopped_in_place_space_form_maps_to_off(self):
    """The API's space-form 'stopped in place' must map to OFF, not UNKNOWN.

    Scaleway returns the state with spaces; the SDK enum's declared value uses
    underscores and passes unknown values through as raw strings. If we don't
    normalize, the instance maps to STATUS_UNKNOWN, is excluded from listings,
    and is never reaped (quota leak).
    """
    from types import SimpleNamespace
    from testflows.runners.providers.scaleway import utils
    from testflows.runners.cloud_provider import CloudProvider

    with Then("state_key normalizes spaces to underscores"):
        assert utils.state_key("stopped in place") == "stopped_in_place"
        assert utils.state_key("STOPPED IN PLACE") == "stopped_in_place"
    with And("a server in the space-form state maps to OFF and is listable"):
        srv = SimpleNamespace(
            id="i", name="github-runner-1-0-dev1.s", state="stopped in place",
            zone="fr-par-1", commercial_type="DEV1-S",
            public_ips=[], public_ip=None, private_ip=None, tags=[], creation_date=None,
        )
        ps = utils._server_to_provider(srv)
        assert ps.status == CloudProvider.STATUS_OFF, ps.status
        assert utils.state_key(srv.state) in utils._ACTIVE_STATES


@TestScenario
def get_server_ssh_key_name_round_trips(self):
    """get_server_ssh_key_name reads back the key name build_server_labels stored.

    Scaleway inherits the base 'github-runner-ssh-key' tag; this guards the label
    divergence that broke the scale_down ownership check for non-Hetzner servers.
    """
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with When("build_server_labels records an ssh key name"):
        labels = provider.build_server_labels(["self-hosted"], ssh_key_name="abc123")
        server = ProviderServer(
            id="i", name="github-runner-1-0-dev1.s", status="off",
            public_ipv4=None, private_ipv4=None, labels=labels,
            server_type="dev1.s", location="fr-par-1", created=None,
        )
    with Then("get_server_ssh_key_name returns that name"):
        assert provider.get_server_ssh_key_name(server) == "abc123"


# ---------------------------------------------------------------------------
# get_image: UUID / marketplace label / custom image by name
# ---------------------------------------------------------------------------


@TestScenario
def get_image_uuid_passthrough(self):
    """An image UUID is returned as-is without any API lookup."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with Then("a UUID resolves to itself"):
        uid = "33333333-3333-3333-3333-333333333333"
        assert provider.get_image(uid) == uid


@TestScenario
def get_image_rejects_foreign_specs(self):
    """Hetzner colon-form and AWS ami- specs raise ImageSpecFormatError."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with Then("foreign specs are flagged so scale_up can try another provider"):
        for foreign in ("x86:system:ubuntu-22.04", "ami-0abc123def", "resolve:ssm:/x"):
            try:
                provider.get_image(foreign)
                assert False, f"expected ImageSpecFormatError for {foreign!r}"
            except ImageSpecFormatError:
                pass


@TestScenario
def get_image_marketplace_label(self):
    """A marketplace label resolves before custom images are consulted."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("marketplace resolution returns an id and custom lookup would fail"):
        provider._resolve_marketplace_image = lambda label: "mkt-" + label

        def _boom(**kwargs):
            raise AssertionError("custom lookup must not run when marketplace matches")

        provider._instance.list_images_all = _boom
    with Then("the marketplace id is returned"):
        assert provider.get_image("ubuntu_jammy") == "mkt-ubuntu_jammy"


@TestScenario
def get_image_custom_dashed_name_skips_marketplace(self):
    """A custom name with '-'/'.' skips the marketplace lookup entirely.

    Reproduces the reported bug: a baked image name like
    'arm-ubuntu-24.04-regression-tester' must not be sent to the marketplace
    endpoint (which 404s), but resolved directly as a custom image.
    """
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("marketplace resolution would fail if called, and a private image exists"):
        def _must_not_call(label):
            raise AssertionError("marketplace must not be queried for a dashed name")

        provider._resolve_marketplace_image = _must_not_call
        provider._instance.list_images_all = lambda **kwargs: [
            _FakeImage(id="img-arm", name="arm-ubuntu-24.04-regression-tester", arch="arm64"),
        ]
    with Then("the custom image id is returned without touching the marketplace"):
        assert provider.get_image("arm-ubuntu-24.04-regression-tester") == "img-arm"


@TestScenario
def get_image_marketplace_shaped_miss_falls_through_to_custom(self):
    """A marketplace-shaped spec that misses (None) falls through to custom images."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("marketplace returns None (unknown label) and a custom image matches"):
        provider._resolve_marketplace_image = lambda label: None
        provider._instance.list_images_all = lambda **kwargs: [
            _FakeImage(id="img-x86", name="ubuntucustom", arch="x86_64"),
        ]
    with Then("the custom image id is returned"):
        assert provider.get_image("ubuntucustom") == "img-x86"


@TestScenario
def get_image_custom_by_name(self):
    """A custom image name resolves to its private-image UUID, preferring x86_64."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("no marketplace match, and a private image exists in two arches"):
        provider._resolve_marketplace_image = lambda label: None
        provider._instance.list_images_all = lambda **kwargs: [
            _FakeImage(id="img-arm", name="runner-base", arch="arm64"),
            _FakeImage(id="img-x86", name="runner-base", arch="x86_64"),
        ]
    with Then("the x86_64 custom image id is returned"):
        assert provider.get_image("runner-base") == "img-x86"


@TestScenario
def get_image_custom_name_requires_exact_match(self):
    """A prefix-only name match is rejected (the API name filter is a prefix)."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("marketplace misses and only a prefix-match private image exists"):
        provider._resolve_marketplace_image = lambda label: None
        provider._instance.list_images_all = lambda **kwargs: [
            _FakeImage(id="img-1", name="runner-base-2024", arch="x86_64"),
        ]
    with Then("get_image raises ImageError (no exact name match)"):
        try:
            provider.get_image("runner-base")
            assert False, "expected ImageError for prefix-only match"
        except ImageError:
            pass


# ---------------------------------------------------------------------------
# Feature entry point
# ---------------------------------------------------------------------------


@TestFeature
@Name("scaleway config")
def feature(self):
    """Scaleway type translation, args validation, config parsing, and factory."""
    for scenario in loads(current_module(), Scenario):
        scenario()
