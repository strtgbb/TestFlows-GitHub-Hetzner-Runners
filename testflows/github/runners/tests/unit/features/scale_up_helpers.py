"""Tests for pure helper functions in scale_up.py."""
from unittest.mock import MagicMock, patch

from testflows.core import *

from testflows.github.runners.cloud_provider import (
    CloudProvider,
    ProviderServer,
    ProviderVolume,
    RecycleRequest,
)
from testflows.github.runners.recycling import recyclable_server_matches
import testflows.github.runners.scale_up as scale_up_mod
from testflows.github.runners.scale_up import (
    RunnerServer,
    check_max_servers_for_label_reached,
    count_available,
    count_available_runners,
    count_present,
    get_job_labels,
    get_runner_server_type,
    get_server_count_with_labels,
    get_stolen_runner_labels,
    get_total_server_count,
    get_volume_name,
    job_matches_labels,
    server_setup,
    set_future_attributes,
)
from github.GithubException import UnknownObjectException
from testflows.github.runners.constants import (
    runner_name_prefix,
    recycle_image_label,
    recycle_server_name_prefix,
)


# ---------------------------------------------------------------------------
# Helpers (plain Python, not @TestStep — they just build objects)
# ---------------------------------------------------------------------------

RUNNER_PREFIX = runner_name_prefix  # "github-runner-"


def _runner_server(
    labels=None,
    server_status=CloudProvider.STATUS_RUNNING,
    status="ready",
    server_type_name="cx22",
    server_location_name="nbg1",
    server_volumes=None,
    native=None,
):
    """Build a minimal RunnerServer for tests."""
    ps = MagicMock()  # ProviderServer
    ps._native = native or MagicMock()
    return RunnerServer(
        name=f"{RUNNER_PREFIX}run1-0-{server_type_name}",
        labels=set(labels or []),
        server_type=server_type_name,
        server_location=server_location_name,
        server_volumes=server_volumes or [],
        server_status=server_status,
        runner_status=status,
        server=ps,
    )


def _gh_runner(status="online", busy=False, labels=None):
    """Build a minimal GitHub SelfHostedActionsRunner mock."""
    r = MagicMock()
    r.status = status
    r.busy = busy
    r.labels = [{"name": lbl} for lbl in (labels or [])]
    return r


# ---------------------------------------------------------------------------
# get_volume_name
# ---------------------------------------------------------------------------


@TestScenario
def get_volume_name_strips_everything_after_first_dash(self):
    assert get_volume_name("cache-x86-ubuntu-22.04-1234567") == "cache"


@TestScenario
def get_volume_name_returned_as_is_without_dash(self):
    assert get_volume_name("mydata") == "mydata"


@TestScenario
def get_volume_name_takes_only_first_segment(self):
    assert get_volume_name("a-b-c") == "a"


# ---------------------------------------------------------------------------
# get_runner_server_type
# ---------------------------------------------------------------------------


def _runner_name(server_type):
    return f"{RUNNER_PREFIX}run1-0-{server_type}"


@TestScenario
def format_runner_name_round_trips_to_type(self):
    """format_runner_name and get_runner_server_type are inverse (the codec)."""
    from testflows.github.runners.utils import format_runner_name

    for t in ("cx22", "c8g.2xlarge", "basic2.a16c.32g", "dev1.s"):
        name = format_runner_name(123, 45, t)
        assert name == f"{RUNNER_PREFIX}123-45-{t}", name
        assert get_runner_server_type(name) == t, name


@TestScenario
def get_runner_server_type_valid(self):
    assert get_runner_server_type(_runner_name("cx22")) == "cx22"


@TestScenario
def get_runner_server_type_aws_with_dot(self):
    assert get_runner_server_type(_runner_name("c8g.2xlarge")) == "c8g.2xlarge"


@TestScenario
def get_runner_server_type_wrong_prefix(self):
    assert get_runner_server_type("other-runner-run1-0-cx22") is None


@TestScenario
def get_runner_server_type_too_few_segments(self):
    assert get_runner_server_type(f"{RUNNER_PREFIX}run1-cx22") is None


@TestScenario
def get_runner_server_type_empty(self):
    assert get_runner_server_type("") is None


@TestScenario
def get_runner_server_type_none(self):
    assert get_runner_server_type(None) is None


# ---------------------------------------------------------------------------
# get_job_labels
# ---------------------------------------------------------------------------


def _job(labels):
    j = MagicMock()
    j.raw_data = {"labels": labels}
    return j


@TestScenario
def get_job_labels_lowercases(self):
    assert get_job_labels(_job(["Self-Hosted", "Linux"])) == ["self-hosted", "linux"]


@TestScenario
def get_job_labels_deduplicates_preserving_order(self):
    assert get_job_labels(_job(["linux", "self-hosted", "linux"])) == ["linux", "self-hosted"]


@TestScenario
def get_job_labels_empty(self):
    assert get_job_labels(_job([])) == []


@TestScenario
def get_job_labels_case_insensitive_dedup(self):
    assert get_job_labels(_job(["Linux", "linux"])) == ["linux"]


# ---------------------------------------------------------------------------
# job_matches_labels
# ---------------------------------------------------------------------------


@TestScenario
def job_matches_labels_none_with_label_matches(self):
    assert job_matches_labels(["linux"], with_label=None) is True


@TestScenario
def job_matches_labels_all_required_present(self):
    assert job_matches_labels(["linux", "self-hosted"], with_label=["linux"]) is True


@TestScenario
def job_matches_labels_missing_required(self):
    result = job_matches_labels(["linux"], with_label=["arm64"])
    assert result == (False, "arm64")


@TestScenario
def job_matches_labels_empty_with_label_matches(self):
    assert job_matches_labels(["linux"], with_label=[]) is True


@TestScenario
def job_matches_labels_multiple_required_all_present(self):
    assert (
        job_matches_labels(["linux", "self-hosted", "arm64"], with_label=["linux", "arm64"]) is True
    )


@TestScenario
def job_matches_labels_multiple_required_one_missing(self):
    result = job_matches_labels(["linux"], with_label=["linux", "arm64"])
    assert result == (False, "arm64")


# ---------------------------------------------------------------------------
# count_available_runners
# ---------------------------------------------------------------------------


@TestScenario
def count_available_runners_empty(self):
    assert count_available_runners([], ["linux"]) == 0


@TestScenario
def count_available_runners_online_not_busy(self):
    r = _gh_runner(status="online", busy=False, labels=["linux", "self-hosted"])
    assert count_available_runners([r], ["linux"]) == 1


@TestScenario
def count_available_runners_offline_not_counted(self):
    r = _gh_runner(status="offline", busy=False, labels=["linux"])
    assert count_available_runners([r], ["linux"]) == 0


@TestScenario
def count_available_runners_busy_not_counted(self):
    r = _gh_runner(status="online", busy=True, labels=["linux"])
    assert count_available_runners([r], ["linux"]) == 0


@TestScenario
def count_available_runners_missing_required_label(self):
    r = _gh_runner(status="online", busy=False, labels=["linux"])
    assert count_available_runners([r], ["linux", "arm64"]) == 0


@TestScenario
def count_available_runners_superset_labels_count(self):
    r = _gh_runner(status="online", busy=False, labels=["linux", "arm64", "self-hosted"])
    assert count_available_runners([r], ["linux", "arm64"]) == 1


@TestScenario
def count_available_runners_multiple_mixed(self):
    runners = [
        _gh_runner(status="online", busy=False, labels=["linux"]),
        _gh_runner(status="online", busy=True, labels=["linux"]),
        _gh_runner(status="offline", busy=False, labels=["linux"]),
    ]
    assert count_available_runners(runners, ["linux"]) == 1


# ---------------------------------------------------------------------------
# count_available / count_present
# ---------------------------------------------------------------------------


@TestScenario
def count_available_empty(self):
    assert count_available([], ["linux"]) == 0


@TestScenario
def count_available_ready_matching(self):
    s = _runner_server(labels=["linux"], status="ready")
    assert count_available([s], ["linux"]) == 1


@TestScenario
def count_available_initializing_counted(self):
    s = _runner_server(labels=["linux"], status="initializing")
    assert count_available([s], ["linux"]) == 1


@TestScenario
def count_available_busy_not_counted(self):
    s = _runner_server(labels=["linux"], status="busy")
    assert count_available([s], ["linux"]) == 0


@TestScenario
def count_available_powered_off_not_counted(self):
    s = _runner_server(labels=["linux"], server_status=CloudProvider.STATUS_OFF)
    assert count_available([s], ["linux"]) == 0


@TestScenario
def count_available_label_mismatch(self):
    s = _runner_server(labels=["linux"], status="ready")
    assert count_available([s], ["arm64"]) == 0


@TestScenario
def count_present_empty(self):
    assert count_present([], ["linux"]) == 0


@TestScenario
def count_present_running_counted(self):
    s = _runner_server(labels=["linux"])
    assert count_present([s], ["linux"]) == 1


@TestScenario
def count_present_powered_off_not_counted(self):
    s = _runner_server(labels=["linux"], server_status=CloudProvider.STATUS_OFF)
    assert count_present([s], ["linux"]) == 0


@TestScenario
def count_present_any_non_off_status(self):
    for status in ("ready", "busy", "initializing"):
        s = _runner_server(labels=["linux"], status=status)
        assert count_present([s], ["linux"]) == 1


@TestScenario
def count_present_label_mismatch(self):
    s = _runner_server(labels=["linux"])
    assert count_present([s], ["arm64"]) == 0


# ---------------------------------------------------------------------------
# get_total_server_count / get_server_count_with_labels
# ---------------------------------------------------------------------------


@TestScenario
def get_total_server_count_no_futures(self):
    assert get_total_server_count(["a", "b", "c"]) == 3


@TestScenario
def get_total_server_count_with_futures(self):
    assert get_total_server_count(["a"], ["f1", "f2"]) == 3


@TestScenario
def get_total_server_count_empty(self):
    assert get_total_server_count([]) == 0


@TestScenario
def get_total_server_count_none_futures(self):
    assert get_total_server_count(["a", "b"], None) == 2


@TestScenario
def get_server_count_with_labels_no_servers(self):
    assert get_server_count_with_labels([], {"linux"}) == 0


@TestScenario
def get_server_count_with_labels_matching_counted(self):
    s = _runner_server(labels=["linux", "self-hosted"])
    assert get_server_count_with_labels([s], {"linux"}) == 1


@TestScenario
def get_server_count_with_labels_non_matching(self):
    s = _runner_server(labels=["linux"])
    assert get_server_count_with_labels([s], {"arm64"}) == 0


@TestScenario
def get_server_count_with_labels_future_matching_counted(self):
    f = MagicMock()
    f.server_labels = {"linux", "self-hosted"}
    assert get_server_count_with_labels([], {"linux"}, futures=[f]) == 1


@TestScenario
def get_server_count_with_labels_future_without_attr_skipped(self):
    f = MagicMock(spec=[])
    assert get_server_count_with_labels([], {"linux"}, futures=[f]) == 0


@TestScenario
def get_server_count_with_labels_combined(self):
    s = _runner_server(labels=["linux"])
    f = MagicMock()
    f.server_labels = {"linux"}
    assert get_server_count_with_labels([s], {"linux"}, futures=[f]) == 2


# ---------------------------------------------------------------------------
# check_max_servers_for_label_reached
# ---------------------------------------------------------------------------


@TestScenario
def check_max_no_limits_configured(self):
    reached, info = check_max_servers_for_label_reached([], {"linux"}, [])
    assert reached is False
    assert info is None


@TestScenario
def check_max_under_limit(self):
    servers = [_runner_server(labels=["linux"])]
    reached, _ = check_max_servers_for_label_reached(
        [(frozenset(["linux"]), 3)], {"linux"}, servers
    )
    assert reached is False


@TestScenario
def check_max_at_limit_returns_true(self):
    servers = [_runner_server(labels=["linux"]), _runner_server(labels=["linux"])]
    reached, info = check_max_servers_for_label_reached(
        [(frozenset(["linux"]), 2)], {"linux"}, servers
    )
    assert reached is True
    assert info[2] == 2


@TestScenario
def check_max_job_labels_not_subset_skipped(self):
    servers = [_runner_server(labels=["linux"])] * 5
    reached, _ = check_max_servers_for_label_reached(
        [(frozenset(["linux", "arm64"]), 1)], {"linux"}, servers
    )
    assert reached is False


@TestScenario
def check_max_futures_counted(self):
    f = MagicMock()
    f.server_labels = {"linux"}
    reached, info = check_max_servers_for_label_reached(
        [(frozenset(["linux"]), 1)], {"linux"}, [], futures=[f]
    )
    assert reached is True


# ---------------------------------------------------------------------------
# set_future_attributes
# ---------------------------------------------------------------------------


@TestScenario
def set_future_attributes_sets_all(self):
    future = MagicMock()
    loc = MagicMock()
    st = MagicMock()
    set_future_attributes(future, "myserver", st, loc, [], {"linux"})
    assert future.server_name == "myserver"
    assert future.server_type is st
    assert future.server_location is loc
    assert future.server_volumes == []
    assert future.server_labels == {"linux"}


# ---------------------------------------------------------------------------
# provider-neutral recyclable matching
# ---------------------------------------------------------------------------


def _recyclable_server(
    type_name="cx22",
    location_name="nbg1",
    volume_names=None,
    ipv4=True,
    ipv6=False,
    ssh_key_label="mykey",
    disk_size=None,
):
    return ProviderServer(
        id="recycled-1",
        name=f"{recycle_server_name_prefix}one",
        status=CloudProvider.STATUS_OFF,
        public_ipv4="192.0.2.1" if ipv4 else None,
        public_ipv6="2001:db8::1" if ipv6 else None,
        private_ipv4=None,
        labels={"ssh-key": ssh_key_label, recycle_image_label: "image"},
        server_type=type_name,
        location=location_name,
        created=MagicMock(),
        root_disk_size=disk_size,
        volumes=[
            ProviderVolume(
                id=name,
                name=name,
                size=10,
                location=location_name,
                labels={},
            )
            for name in (volume_names or [])
        ],
    )


def _recycle_request(
    type_name="cx22",
    location_name="nbg1",
    volume_names=None,
    ipv4=True,
    ipv6=False,
    ssh_key_name="mykey",
    min_disk=None,
):
    return RecycleRequest(
        name="github-runner-new",
        server_type=type_name,
        location=location_name,
        image="image",
        labels={recycle_image_label: "image"},
        ssh_key_names=frozenset({ssh_key_name}),
        volume_names=frozenset(volume_names or []),
        enable_ipv4=ipv4,
        enable_ipv6=ipv6,
        min_disk=min_disk,
    )


def _recycle_provider(supports_volumes=True):
    provider = MagicMock()
    provider.supports_volumes = supports_volumes
    provider.is_recycled_server.side_effect = (
        lambda server: server.name.startswith(recycle_server_name_prefix)
    )
    provider.has_matching_ssh_key.side_effect = (
        lambda server, names: server.labels.get("ssh-key") in names
    )
    provider.get_server_tag.side_effect = lambda server, key: server.labels.get(key)
    return provider


@TestScenario
def recyclable_full_match(self):
    server = _recyclable_server()
    assert recyclable_server_matches(
        _recycle_provider(), server, _recycle_request()
    ) is True


@TestScenario
def recyclable_provider_attributes_must_match(self):
    provider = _recycle_provider()
    server = _recyclable_server()
    assert recyclable_server_matches(
        provider, server, _recycle_request(type_name="cx32")
    ) is False
    assert recyclable_server_matches(
        provider, server, _recycle_request(location_name="fsn1")
    ) is False
    assert recyclable_server_matches(
        provider, server, _recycle_request(location_name=None)
    ) is True


@TestScenario
def recyclable_volume_mismatch(self):
    server = _recyclable_server(volume_names=["data"])
    assert recyclable_server_matches(
        _recycle_provider(), server, _recycle_request(volume_names=["other"])
    ) is False


@TestScenario
def recyclable_ignores_volumes_when_provider_lacks_support(self):
    """A provider without volume support reuses a volume-less pooled server even
    when the job requests a caching volume — the request is ignored (a caching
    optimisation), so the server is still a valid reuse candidate."""
    server = _recyclable_server(volume_names=[])
    assert recyclable_server_matches(
        _recycle_provider(supports_volumes=False),
        server,
        _recycle_request(volume_names=["cache"]),
    ) is True


@TestScenario
def recyclable_image_must_match_without_rebuild(self):
    server = _recyclable_server()
    request = _recycle_request()
    request.labels[recycle_image_label] = "different-image"
    assert recyclable_server_matches(
        _recycle_provider(), server, request
    ) is False


@TestScenario
def recyclable_network_and_key_must_match(self):
    provider = _recycle_provider()
    assert recyclable_server_matches(
        provider, _recyclable_server(ipv4=False), _recycle_request(ipv4=True)
    ) is False
    assert recyclable_server_matches(
        provider, _recyclable_server(ipv6=True), _recycle_request(ipv6=False)
    ) is False
    server = _recyclable_server(ssh_key_label="oldkey")
    assert recyclable_server_matches(
        provider, server, _recycle_request(ssh_key_name="newkey")
    ) is False


@TestScenario
def recyclable_min_disk_reuses_only_known_safe(self):
    """A disk- minimum reuses a pooled server only when its disk is known >= it."""
    provider = _recycle_provider()
    with When("no minimum is requested"):
        with Then("a pooled server with unknown disk still matches (today's behavior)"):
            assert recyclable_server_matches(
                provider, _recyclable_server(disk_size=None), _recycle_request()
            ) is True
    with When("the pooled server's disk is known to meet the minimum"):
        with Then("it matches"):
            assert recyclable_server_matches(
                provider,
                _recyclable_server(disk_size=160),
                _recycle_request(min_disk=100),
            ) is True
            assert recyclable_server_matches(
                provider,
                _recyclable_server(disk_size=100),
                _recycle_request(min_disk=100),
            ) is True
    with When("the pooled server's disk is smaller than the minimum"):
        with Then("it does not match"):
            assert recyclable_server_matches(
                provider,
                _recyclable_server(disk_size=40),
                _recycle_request(min_disk=100),
            ) is False
    with When("the pooled server's disk is unknown but a minimum is requested"):
        with Then("it is not known-safe, so it does not match"):
            assert recyclable_server_matches(
                provider,
                _recyclable_server(disk_size=None),
                _recycle_request(min_disk=100),
            ) is False


# ---------------------------------------------------------------------------
# server_setup: generic post-setup hook is always invoked
# ---------------------------------------------------------------------------


@TestScenario
def server_setup_reports_success_to_provider(self):
    provider = MagicMock()
    server = MagicMock()
    with patch.object(scale_up_mod, "_run_server_setup"):
        server_setup(
            provider=provider,
            server=server,
            setup_script="setup.sh",
            startup_script="startup.sh",
            github_token="token",
            github_repository="owner/repo",
            runner_labels="self-hosted",
        )
    provider.after_server_setup.assert_called_once()
    setup_server, error = provider.after_server_setup.call_args.args
    assert setup_server is server
    assert error is None


@TestScenario
def server_setup_reports_original_failure_to_provider(self):
    provider = MagicMock()
    server = MagicMock()
    boom = RuntimeError("setup blew up")
    with patch.object(scale_up_mod, "_run_server_setup", side_effect=boom):
        raised = None
        try:
            server_setup(
                provider=provider,
                server=server,
                setup_script="setup.sh",
                startup_script="startup.sh",
                github_token="token",
                github_repository="owner/repo",
                runner_labels="self-hosted",
            )
        except RuntimeError as e:
            raised = e
    assert raised is boom, "setup exception must propagate"
    provider.after_server_setup.assert_called_once()
    setup_server, error = provider.after_server_setup.call_args.args
    assert setup_server is server
    assert error is boom


@TestScenario
def post_setup_hook_failure_does_not_mask_setup_outcome(self):
    provider = MagicMock()
    provider.name = "broken"
    provider.after_server_setup.side_effect = RuntimeError("hook failed")
    server = MagicMock()
    setup_error = RuntimeError("setup failed")

    with patch.object(scale_up_mod, "_run_server_setup", side_effect=setup_error):
        try:
            server_setup(
                provider=provider,
                server=server,
                setup_script="setup.sh",
                startup_script="startup.sh",
                github_token="token",
                github_repository="owner/repo",
                runner_labels="self-hosted",
            )
        except RuntimeError as raised:
            assert raised is setup_error
        else:
            assert False, "setup failure must propagate"

    with patch.object(scale_up_mod, "_run_server_setup"):
        server_setup(
            provider=provider,
            server=server,
            setup_script="setup.sh",
            startup_script="startup.sh",
            github_token="token",
            github_repository="owner/repo",
            runner_labels="self-hosted",
        )
    assert provider.after_server_setup.call_count == 2


@TestScenario
def post_setup_base_exception_does_not_mask_setup_failure(self):
    provider = MagicMock()
    provider.name = "broken"
    provider.after_server_setup.side_effect = KeyboardInterrupt()
    server = MagicMock()
    setup_error = RuntimeError("setup failed")

    with patch.object(scale_up_mod, "_run_server_setup", side_effect=setup_error):
        try:
            server_setup(
                provider=provider,
                server=server,
                setup_script="setup.sh",
                startup_script="startup.sh",
                github_token="token",
                github_repository="owner/repo",
                runner_labels="self-hosted",
            )
        except RuntimeError as raised:
            assert raised is setup_error
        else:
            assert False, "setup failure must remain primary"


# ---------------------------------------------------------------------------
# get_stolen_runner_labels: standby replenishment survives a deregistered runner
# ---------------------------------------------------------------------------


@TestScenario
def stolen_runner_labels_returns_lowercased_deduped(self):
    """Labels of the runner a job stole are returned lowercased and de-duplicated."""
    repo = MagicMock()
    runner = MagicMock()
    runner.labels = [
        {"name": "Self-Hosted"},
        {"name": "type-cx22"},
        {"name": "self-hosted"},
    ]
    repo.get_self_hosted_runner.return_value = runner
    with When("the runner still exists"):
        labels = get_stolen_runner_labels(repo, 123)
    with Then("its labels come back lowercased with duplicates removed"):
        assert labels == ["self-hosted", "type-cx22"], labels
        repo.get_self_hosted_runner.assert_called_once_with(123)


@TestScenario
def stolen_runner_labels_none_when_runner_gone(self):
    """A 404 (runner already deregistered) yields None instead of raising."""
    repo = MagicMock()
    repo.get_self_hosted_runner.side_effect = UnknownObjectException(
        404, {"message": "Not Found"}, None
    )
    with When("the runner was deregistered between snapshot and lookup"):
        labels = get_stolen_runner_labels(repo, 999)
    with Then("None is returned so the caller can skip replenishment"):
        assert labels is None, labels


# ---------------------------------------------------------------------------
# Feature entry point
# ---------------------------------------------------------------------------


@TestFeature
@Name("scale_up helpers")
def feature(self):
    """Pure helper functions in scale_up.py."""
    for scenario in loads(current_module(), Scenario):
        scenario()
