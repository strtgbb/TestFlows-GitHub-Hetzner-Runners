"""Tests for derive_runner_tag — the controller-identity discovery-tag value."""
from testflows.core import *

from testflows.runners.utils import derive_runner_tag


@TestScenario
def basic_repo_and_label(self):
    """repo + a single label, sanitized (slash -> dash, lowercased)."""
    assert derive_runner_tag("acme/infra", ["self-hosted"]) == "acme-infra-self-hosted"


@TestScenario
def label_order_independent(self):
    """with_label order does not change the id (sorted)."""
    a = derive_runner_tag("acme/infra", ["gpu", "self-hosted"])
    b = derive_runner_tag("acme/infra", ["self-hosted", "gpu"])
    assert a == b == "acme-infra-gpu-self-hosted", (a, b)


@TestScenario
def sanitizes_disallowed_chars(self):
    """Uppercase and out-of-charset runs collapse to a single dash."""
    tag = derive_runner_tag("Acme/Infra_2", ["Self Hosted!!"])
    assert tag == "acme-infra_2-self-hosted", tag
    import re

    assert re.fullmatch(r"[a-z0-9._-]+", tag), tag


@TestScenario
def strips_leading_trailing_dashes(self):
    tag = derive_runner_tag("/acme/", ["-gpu-"])
    assert not tag.startswith("-") and not tag.endswith("-"), tag


@TestScenario
def long_id_truncates_with_stable_hash_suffix(self):
    """Over-length ids are truncated and hash-suffixed, deterministically."""
    repo = "some-really-long-org-name/an-extremely-long-repository-name-that-blows-past"
    labels = ["self-hosted", "extra-long-label-value-here"]
    a = derive_runner_tag(repo, labels)
    b = derive_runner_tag(repo, labels)
    assert a == b, (a, b)  # deterministic
    assert len(a) <= 63, len(a)
    import re

    assert re.search(r"-[0-9a-f]{8}$", a), a  # hash suffix present


@TestScenario
def different_inputs_differ(self):
    """Different repo or label set -> different id (isolation)."""
    base = derive_runner_tag("acme/infra", ["self-hosted"])
    assert derive_runner_tag("acme/other", ["self-hosted"]) != base
    assert derive_runner_tag("acme/infra", ["self-hosted", "gpu"]) != base


@TestScenario
def tolerates_empty_labels(self):
    assert derive_runner_tag("acme/infra", []) == "acme-infra"
    assert derive_runner_tag("acme/infra", None) == "acme-infra"


@TestFeature
@Name("runner tag")
def feature(self):
    """derive_runner_tag controller-identity helper."""
    for scenario in loads(current_module(), Scenario):
        scenario()
