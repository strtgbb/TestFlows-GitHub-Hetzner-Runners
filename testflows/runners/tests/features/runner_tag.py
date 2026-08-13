"""Tests for derive_runner_tag — the controller-identity discovery-tag value.

The id is a sanitized human-readable part plus an always-appended `-<8 hex>`
hash of the unsanitized identity (collision-free, valid label value).
"""
import re

from testflows.core import *

from testflows.runners.utils import derive_runner_tag

_SUFFIX = re.compile(r"-[0-9a-f]{8}$")


@TestScenario
def basic_repo_and_label(self):
    """repo + a single label, sanitized (slash -> dash, lowercased), + hash."""
    tag = derive_runner_tag("acme/infra", ["self-hosted"])
    assert tag.startswith("acme-infra-self-hosted-"), tag
    assert _SUFFIX.search(tag), tag


@TestScenario
def label_order_independent(self):
    """with_label order does not change the id (sorted)."""
    a = derive_runner_tag("acme/infra", ["gpu", "self-hosted"])
    b = derive_runner_tag("acme/infra", ["self-hosted", "gpu"])
    assert a == b, (a, b)
    assert a.startswith("acme-infra-gpu-self-hosted-"), a


@TestScenario
def valid_label_charset_and_ends(self):
    """Whole id is within the charset and starts/ends alphanumeric."""
    tag = derive_runner_tag("Acme/Infra_2", ["Self Hosted!!"])
    assert re.fullmatch(r"[a-z0-9._-]+", tag), tag
    assert tag[0].isalnum() and tag[-1].isalnum(), tag
    assert tag.startswith("acme-infra_2-self-hosted-"), tag


@TestScenario
def strips_leading_trailing_separators(self):
    """Leading/trailing separators from the inputs are stripped (valid ends)."""
    tag = derive_runner_tag("/acme./", ["-_gpu_-"])
    assert tag[0].isalnum() and tag[-1].isalnum(), tag


@TestScenario
def deterministic_and_length_capped(self):
    repo = "some-really-long-org-name/an-extremely-long-repository-name-that-blows-past"
    labels = ["self-hosted", "extra-long-label-value-here"]
    a = derive_runner_tag(repo, labels)
    b = derive_runner_tag(repo, labels)
    assert a == b, (a, b)
    assert len(a) <= 63, len(a)
    assert _SUFFIX.search(a), a


@TestScenario
def hash_disambiguates_sanitization_collisions(self):
    """Inputs that sanitize to the same readable form still differ (via hash)."""
    a = derive_runner_tag("acme/infra", ["gpu", "self-hosted"])
    b = derive_runner_tag("acme/infra-gpu", ["self-hosted"])
    # same readable prefix ...
    assert a.rsplit("-", 1)[0] == b.rsplit("-", 1)[0], (a, b)
    # ... different hash suffix
    assert a != b, (a, b)


@TestScenario
def different_inputs_differ(self):
    base = derive_runner_tag("acme/infra", ["self-hosted"])
    assert derive_runner_tag("acme/other", ["self-hosted"]) != base
    assert derive_runner_tag("acme/infra", ["self-hosted", "gpu"]) != base


@TestScenario
def tolerates_empty_labels(self):
    tag = derive_runner_tag("acme/infra", [])
    assert tag.startswith("acme-infra-") and _SUFFIX.search(tag), tag
    assert derive_runner_tag("acme/infra", None) == tag


@TestScenario
def empty_identity_falls_back_to_hash(self):
    """A fully-empty identity still yields a valid (hash-only) tag."""
    tag = derive_runner_tag("", [])
    assert re.fullmatch(r"[0-9a-f]{8}", tag), tag


@TestFeature
@Name("runner tag")
def feature(self):
    """derive_runner_tag controller-identity helper."""
    for scenario in loads(current_module(), Scenario):
        scenario()
