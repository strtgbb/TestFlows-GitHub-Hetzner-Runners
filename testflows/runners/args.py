# Copyright 2023 Katteli Inc.
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
import os
import sys

from argparse import ArgumentTypeError
from traceback import print_exception

# Pure validators live in the leaf ``argtypes`` module; re-exported here so
# existing ``args.<name>_type`` references (bin parser, providers) keep working.
from .argtypes import (
    file_type,
    ColumnsType,
    lines_type,
    columns_type,
    end_of_life_type,
    switch_type,
    path_type,
    count_type,
    image_type,
    location_type,
    server_type,
    meta_label_type,
    max_runners_for_label_type,
    provider_type,
)


def config_type(v):
    """Program configuration file type."""
    from .config import default_user_config
    from .config.parse import parse_config

    if v == "__default_user_config__":
        if os.path.exists(default_user_config):
            v = default_user_config
        else:
            return None

    v = path_type(v)
    try:
        config = parse_config(v)
        config.config_file = v
    except Exception as e:
        if "--debug" in sys.argv:
            print_exception(e)
        if "unexpected keyword argument" in str(e):
            e = str(e).replace(".__init__()", "") + ", please remove it"
        raise ArgumentTypeError(str(e))

    return config
