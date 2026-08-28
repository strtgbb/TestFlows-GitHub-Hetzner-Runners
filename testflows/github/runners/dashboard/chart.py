# Copyright 2025 Katteli Inc.
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

"""Common chart creation utilities for dashboard panels."""

import altair as alt
import pandas as pd
import streamlit as st


def create_time_series_chart(
    chart_id,
    df,
    group_by,
    x_column="Time",
    y_column="Count",
    title="Time Series Chart",
    y_title=None,
    names=None,
    colors=None,
    height=300,
    time_window_minutes=15,
    y_type="count",
):
    """Create a standardized time series chart using Altair.

    Args:
        chart_id: Unique identifier for the chart (required for state management)
        df: Pandas DataFrame with time series data
        x_column: Column name for x-axis (time) or index name if time is in index
        y_column: Column name for y-axis (values)
        group_by: Column name for grouping data into multiple series (required)
        title: Chart title (defaults to "Time Series Chart")
        y_title: Y-axis title (defaults to y_column)
        names: List of series names in the column (optional)
        colors: List of colors for each series name (optional)
        height: Chart height in pixels
        time_window_minutes: Time window to display in minutes
        y_type: Type of y-axis data ("count" for integers, "price" for floats)

    Returns:
        (spec, df) for st.vega_lite_chart, or None if the window is empty.
    """
    if df.empty:
        return None

    # Handle case where time is in the index
    if x_column in df.index.names or (df.index.name == x_column):
        # Reset index to make time a column
        df = df.reset_index()

    if not df.empty and df[x_column].dt.tz is not None:
        current_time = pd.Timestamp.now(tz=df[x_column].dt.tz)
    else:
        current_time = pd.Timestamp.now()

    time_window_start = current_time - pd.Timedelta(minutes=time_window_minutes)
    window_df = df[df[x_column] >= time_window_start].copy()

    if window_df.empty:
        return None

    x_encoding = alt.X(
        f"{x_column}:T",
        title="Time",
        axis=alt.Axis(format="%H:%M", tickCount=15),
    )

    if y_type == "price":
        y_encoding = alt.Y(
            f"{y_column}:Q",
            title=y_title or y_column,
            axis=alt.Axis(format=".3f", tickCount=6),
        )
    else:
        y_encoding = alt.Y(
            f"{y_column}:Q",
            title=y_title or y_column,
            axis=alt.Axis(format="d", tickCount=6),
        )

    # Tooltip configuration
    tooltip = [
        alt.Tooltip(f"{x_column}:T", title="Time", format="%H:%M:%S"),
        alt.Tooltip(
            f"{y_column}:Q",
            title=y_title or y_column,
            format=".3f" if y_type == "price" else "d",
        ),
    ]

    # The pills selector owns which series are visible; an empty selection
    # yields an empty frame via isin([]), so no special-case is needed.
    visible_names = create_series_selector(names, chart_id)

    if names:
        filtered_df = window_df[window_df[group_by].isin(visible_names)].copy()
    else:
        filtered_df = window_df  # No series names, show all data

    # Build chart encoding
    chart_encoding = {
        "x": x_encoding,
        "y": y_encoding,
        "tooltip": tooltip,
    }

    # Add color encoding if we have multiple series
    if names:
        color_encoding = alt.Color(
            f"{group_by}:N", scale=alt.Scale(domain=names, range=colors)
        )
        chart_encoding["color"] = color_encoding
        tooltip.append(alt.Tooltip(f"{group_by}:N", title=group_by.title()))

    # NamedData keeps rows out of the spec so Streamlit can updateView
    # instead of embed()-ing a new Vega view every refresh.
    chart = (
        alt.Chart(alt.NamedData("source"))
        .mark_line()
        .encode(**chart_encoding)
        .configure_axis(grid=True, gridColor="lightgray", gridOpacity=0.5)
        .properties(
            width="container",
            height=height,
            usermeta={"chart_id": chart_id},
        )
        .resolve_scale(color="independent")
    )
    spec = chart.to_dict()
    spec.pop("datasets", None)
    spec["data"] = {"name": "source"}
    return spec, filtered_df


def create_series_selector(names, chart_id):
    """Render the series-visibility pills and return the visible series names.

    The pills widget's own session-state entry holds the visible set. Before
    rendering, reconcile it with the current series: drop series that are gone
    (so stale entries do not accumulate) and select series that appeared since
    the last render (so a new series shows up visible instead of hidden). A
    memo of the previously seen options tells a genuinely new series apart from
    one the user has deliberately deselected, so toggles survive refreshes.
    """
    if not names:
        return list(names)

    pills_key = f"{chart_id}_legend_pills"
    known_key = f"{chart_id}_legend_known"
    labels = {name: name[0].capitalize() + name[1:] for name in names}
    options = list(labels.values())

    if pills_key not in st.session_state:
        st.session_state[pills_key] = list(options)
    else:
        known = st.session_state.get(known_key, [])
        selected = [o for o in st.session_state[pills_key] if o in options]
        new = [o for o in options if o not in known and o not in selected]
        st.session_state[pills_key] = selected + new
    st.session_state[known_key] = options

    chosen = set(
        st.pills(
            "Currently selected",
            options=options,
            key=pills_key,
            selection_mode="multi",
            width="content",
        )
        or []
    )
    return [name for name, label in labels.items() if label in chosen]
