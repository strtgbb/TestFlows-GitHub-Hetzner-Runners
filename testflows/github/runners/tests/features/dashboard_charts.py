"""Guards for dashboard time-series charts: stable Vega-Lite spec, data aside.

Streamlit re-embeds a new Vega view whenever the spec JSON changes. Altair's
path hashes the dataframe into the spec; baking wall-clock / y_max into
encodings does the same. Charts must send a constant spec and pass rows
separately via st.vega_lite_chart.
"""
import ast
import json
import os
import re
from unittest.mock import MagicMock, patch

import pandas as pd

from testflows.core import *

from testflows.github.runners.dashboard import chart as dashboard_chart

_REPO_ROOT = os.path.abspath(os.path.join(current_dir(), "..", "..", "..", "..", ".."))
_DASHBOARD = os.path.join(_REPO_ROOT, "testflows", "github", "runners", "dashboard")
_MD5_NAME = re.compile(r"^[0-9a-f]{32}$")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _is_st_fragment(decorator):
    node = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(node, ast.Name):
        return node.id == "fragment"
    if isinstance(node, ast.Attribute):
        return node.attr == "fragment"
    return False


def _has_run_every(decorator):
    if not isinstance(decorator, ast.Call):
        return False
    return any(kw.arg == "run_every" for kw in decorator.keywords)


def _fragment_functions(path):
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    filename = os.path.basename(path)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if _is_st_fragment(dec):
                found.append((filename, node.name, node.lineno, _has_run_every(dec)))
                break
    return found


def _scan_dashboard():
    found = []
    for root, _dirs, names in os.walk(_DASHBOARD):
        for name in names:
            if name.endswith(".py"):
                found.extend(_fragment_functions(os.path.join(root, name)))
    return found


def _sample_df(offset_minutes, count_value):
    now = pd.Timestamp.now(tz="UTC")
    t = now - pd.Timedelta(minutes=offset_minutes)
    return pd.DataFrame(
        {
            "Time": [t, t + pd.Timedelta(seconds=30)],
            "Count": [count_value, count_value + 1],
            "Status": ["running", "running"],
        }
    )


def _make_chart(df, names=None, colors=None):
    fake_st = MagicMock()
    fake_st.session_state = {}
    if names:
        fake_st.pills.return_value = [n[0].capitalize() + n[1:] for n in names]
    with patch.object(dashboard_chart, "st", fake_st):
        return dashboard_chart.create_time_series_chart(
            chart_id="cost",
            df=df,
            group_by="Status",
            names=names or [],
            colors=colors,
            y_type="count",
        )


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


@TestScenario
def spec_identical_across_data_changes(self):
    """Two windows of different points must produce the same Vega-Lite spec."""
    a = _make_chart(_sample_df(1, 3), names=["running", "queued"], colors=["#0", "#1"])
    b = _make_chart(_sample_df(2, 9), names=["running", "queued"], colors=["#0", "#1"])
    assert isinstance(a, tuple) and len(a) == 2, type(a)
    assert isinstance(b, tuple) and len(b) == 2, type(b)
    spec_a, df_a = a
    spec_b, df_b = b
    assert not df_a.equals(df_b)
    assert json.dumps(spec_a, sort_keys=True) == json.dumps(spec_b, sort_keys=True)


@TestScenario
def spec_has_named_source_not_inline_data(self):
    """Data lives beside the spec, not hashed or inlined into it."""
    spec, df = _make_chart(_sample_df(1, 3), names=["running"], colors=["#0"])
    assert not df.empty
    assert "datasets" not in spec, spec.get("datasets")
    data = spec.get("data")
    assert data == {"name": "source"}, data
    for node in _walk(spec):
        name = node.get("name")
        if isinstance(name, str):
            assert not _MD5_NAME.match(name), name


@TestScenario
def spec_has_no_datetime_or_ymax_domain(self):
    """Encodings must not bake wall-clock or computed y_max into the spec."""
    spec, _df = _make_chart(_sample_df(1, 3))
    encoding = spec.get("encoding") or {}
    for channel in ("x", "y"):
        enc = encoding.get(channel) or {}
        scale = enc.get("scale") or {}
        assert "domain" not in scale, (channel, scale)
        axis = enc.get("axis") or {}
        assert "values" not in axis, (channel, axis)


@TestScenario
def render_uses_vega_lite_not_altair(self):
    """st.altair_chart hashes data into the spec; vega_lite_chart does not."""
    renderers = _read(os.path.join(_DASHBOARD, "renderers.py"))
    chart_src = _read(os.path.join(_DASHBOARD, "chart.py"))
    assert "vega_lite_chart" in renderers, "render_chart must call st.vega_lite_chart"
    assert "altair_chart" not in renderers
    assert "altair_chart" not in chart_src


@TestScenario
def only_render_page_is_a_fragment(self):
    """Nested fragments accumulate; the page timer is the only allowed fragment."""
    found = _scan_dashboard()
    extra = [
        f"{filename}:{name} (line {lineno})"
        for filename, name, lineno, _run_every in found
        if not (filename == "dashboard.py" and name == "render_page")
    ]
    assert not extra, "unexpected @st.fragment:\n" + "\n".join(extra)
    match = [item for item in found if item[0] == "dashboard.py" and item[1] == "render_page"]
    assert match, found
    assert match[0][3], "render_page must use run_every"
    dashboard_src = _read(os.path.join(_DASHBOARD, "dashboard.py"))
    assert "location.reload" not in dashboard_src


@TestScenario
def tab_render_does_not_use_spinner(self):
    """st.spinner is a transient node; it shifts layout and Streamlit 1.49
    re-embeds Vega whenever measured width changes."""
    renderers = _read(os.path.join(_DASHBOARD, "renderers.py"))
    assert "st.spinner" not in renderers
    dashboard_src = _read(os.path.join(_DASHBOARD, "dashboard.py"))
    assert "st.spinner" not in dashboard_src


@TestFeature
@Name("dashboard charts")
def feature(self):
    """Stable Vega-Lite spec for dashboard time-series charts."""
    for scenario in loads(current_module(), Scenario):
        scenario()
