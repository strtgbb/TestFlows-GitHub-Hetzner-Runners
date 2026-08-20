#!/usr/bin/env python3
"""TestFlows regression entry point for tfs-github-runners unit tests.

Run with:
    python3 regression.py
    python3 regression.py --only "/runners/aws config/*"
"""
import os
import sys

from testflows.core import *

# Ensure the repo root is importable so `testflows.github.runners.*` resolves
# regardless of where the script is invoked from.
append_path(sys.path, os.path.abspath(os.path.join(current_dir(), "..", "..", "..", "..", "..")))


@TestModule
@Name("runners")
def regression(self):
    """tfs-github-runners unit-test regression."""
    Feature(run=load("testflows.github.runners.tests.unit.features.aws_config", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.aws_provider", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.scaleway_config", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.hetzner_provider", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.hetzner_config", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.dedicated_static_provider", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.provider_interface", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.provider_orchestration_hooks", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.cli_and_config", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.estimate", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.scale_up_helpers", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.scale_up_labels", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.scale_down_recycle", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.recycling_lifecycle", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.metrics_cost", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.cloud_deploy", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.servers_cli", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.import_layering", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.dashboard_charts", "feature"))
    Feature(run=load("testflows.github.runners.tests.unit.features.runner_tag", "feature"))


if main():
    regression()
