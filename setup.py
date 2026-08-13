#!/usr/bin/env python3
# Copyright 2023-2025 Katteli Inc.
# TestFlows.com Open-Source Software Testing Framework (http://testflows.com)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from setuptools import setup, find_namespace_packages

with open("README.rst", "r", encoding="utf-8") as fd:
    long_description = fd.read()

# CI substitutes the version placeholder at release time. When it has not been
# substituted (local / editable installs) it does not start with a digit, so
# fall back to a valid PEP 440 dev version. Avoid a second literal placeholder
# token here so the release-time global substitution only touches the line below.
version = "__VERSION__"
if not version[:1].isdigit():
    version = "0.0.0.dev0"


setup(
    name="testflows.github.runners",
    version=version,
    description="Autoscaling GitHub Actions Runners",
    author="Vitaliy Zakaznikov",
    author_email="vzakaznikov@testflows.com",
    long_description=long_description,
    long_description_content_type="text/x-rst",
    url="https://github.com/testflows/testflows-github-hetzner-runners",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: POSIX :: Linux",
    ],
    python_requires=">=3.8",
    license="Apache-2.0",
    # testflows and testflows.github are PEP 420 namespace packages (no
    # __init__.py) shared with sibling testflows.* distributions, so discover
    # the tree instead of hand-listing it (a hand list silently drops new
    # subpackages). tests/ is excluded — it ships in git, not the wheel.
    packages=find_namespace_packages(
        include=["testflows.github.runners", "testflows.github.runners.*"],
        exclude=["testflows.github.runners.tests", "testflows.github.runners.tests.*"],
    ),
    package_data={
        "testflows.github.runners.config": ["*.json"],
        "testflows.github.runners.scripts": ["*.sh"],
        "testflows.github.runners.scripts.deploy": ["*.sh"],
        "testflows.github.runners.bin": ["tfs-github-runners"],
    },
    scripts=["testflows/github/runners/bin/tfs-github-runners"],
    zip_safe=False,
    install_requires=[
        "PyGithub==2.8.1",
        "hcloud==2.3.0",
        "requests-cache==1.2.1",
        "PyYAML==6.0.3",
        "prometheus_client==0.24.1",
        "streamlit==1.49.1",
        "psutil>=7.2.1",
    ],
    extras_require={
        "aws": ["boto3>=1.34"],
        "scaleway": ["scaleway>=2.0"],
        "dev": [],
    },
)
