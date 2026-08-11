#!/usr/bin/env python3
"""TestFlows regression entry point for tfs-runners unit tests.

Run with:
    python3 regression.py
    python3 regression.py --only "/runners/aws config/*"
"""
import os
import sys

from testflows.core import *

# Ensure the repo root is importable so `testflows.runners.*` resolves
# regardless of where the script is invoked from.
append_path(sys.path, os.path.abspath(os.path.join(current_dir(), "..", "..", "..")))


@TestModule
@Name("runners")
def regression(self):
    """tfs-runners unit-test regression."""
    Feature(run=load("testflows.runners.tests.features.aws_config", "feature"))
    Feature(run=load("testflows.runners.tests.features.aws_provider", "feature"))
    Feature(run=load("testflows.runners.tests.features.scaleway_config", "feature"))
    Feature(run=load("testflows.runners.tests.features.hetzner_provider", "feature"))
    Feature(run=load("testflows.runners.tests.features.hetzner_config", "feature"))
    Feature(run=load("testflows.runners.tests.features.dedicated_static_provider", "feature"))
    Feature(run=load("testflows.runners.tests.features.provider_interface", "feature"))
    Feature(run=load("testflows.runners.tests.features.provider_orchestration_hooks", "feature"))
    Feature(run=load("testflows.runners.tests.features.cli_and_config", "feature"))
    Feature(run=load("testflows.runners.tests.features.scale_up_helpers", "feature"))
    Feature(run=load("testflows.runners.tests.features.scale_up_labels", "feature"))
    Feature(run=load("testflows.runners.tests.features.scale_down_recycle", "feature"))
    Feature(run=load("testflows.runners.tests.features.recycling_lifecycle", "feature"))
    Feature(run=load("testflows.runners.tests.features.metrics_cost", "feature"))
    Feature(run=load("testflows.runners.tests.features.cloud_deploy", "feature"))


if main():
    regression()
