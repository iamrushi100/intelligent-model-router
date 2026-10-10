"""Optional beginner-friendly UI for the existing FastAPI router."""

import os
import sys
from pathlib import Path

import streamlit as st

# Streamlit may execute this file with `frontend/` rather than the repository
# root on sys.path. Add the root so both `frontend.api_client` and `app.schemas`
# resolve reliably when launched from the repository root.
PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from frontend.api_client import (
    SUPPORTED_SENSITIVITIES,
    SUPPORTED_TASKS,
    RouterAPIError,
    build_route_payload,
    send_route_request,
)


st.set_page_config(page_title="Intelligent Model Router", layout="centered")
st.title("Intelligent Model Router")
st.caption("A simple interface for the rule-based FastAPI routing demo.")

st.info(
    "**Privacy reminder:** Do not submit real personal, confidential, or sensitive data. "
    "Sensitivity checks are heuristic and are not complete data-loss prevention."
)
st.warning(
    "**Simulated provider:** The backend uses deterministic mock responses. "
    "No real language model is called."
)

with st.sidebar:
    st.header("Connection")
    api_base_url = st.text_input(
        "FastAPI base URL",
        value=os.getenv("ROUTER_API_BASE_URL", "http://127.0.0.1:8000"),
        help="The address where the existing FastAPI server is running.",
    )
    api_key = st.text_input(
        "API key",
        type="password",
        help="Entered for this Streamlit session and sent in the X-API-Key header.",
    )

with st.form("route_request"):
    prompt = st.text_area(
        "Prompt",
        placeholder="For example: Classify this fictional item: blue widget.",
        height=150,
    )
    task_type = st.selectbox("Task", SUPPORTED_TASKS)
    sensitivity = st.selectbox("Declared sensitivity", SUPPORTED_SENSITIVITIES)
    submitted = st.form_submit_button("Route request", type="primary")

if submitted:
    try:
        payload = build_route_payload(prompt, task_type, sensitivity)
        result = send_route_request(api_base_url, api_key, payload)
    except ValueError as exc:
        st.error(str(exc))
    except RouterAPIError as exc:
        prefix = f"HTTP {exc.status_code}: " if exc.status_code is not None else ""
        st.error(prefix + str(exc))
    else:
        st.subheader("Router result")
        st.write("**Simulated output**")
        st.code(result.get("output", "No output field returned."), language=None)

        first_row = st.columns(3)
        first_row[0].metric("Model", result.get("model_used", "Not returned"))
        estimated_cost = result.get("estimated_cost_usd")
        cost_text = f"${estimated_cost:.8f}" if isinstance(estimated_cost, (int, float)) else "Not returned"
        first_row[1].metric("Estimated cost", cost_text)
        latency = result.get("latency_ms")
        latency_text = f"{latency:.2f} ms" if isinstance(latency, (int, float)) else "Not returned"
        first_row[2].metric("Latency", latency_text)

        second_row = st.columns(3)
        second_row[0].metric("Effective sensitivity", result.get("final_sensitivity", "Not returned"))
        second_row[1].metric("Fallback used", str(result.get("fallback_used", "Not returned")))
        second_row[2].metric("Attempts", str(result.get("attempt_count", "Not returned")))

        st.caption(
            "The API response does not include a model tier, so the interface does not infer one."
        )
        if result.get("decision_reason"):
            st.write("**Routing reason**")
            st.write(result["decision_reason"])

        with st.expander("Additional returned usage fields"):
            st.json(
                {
                    name: result[name]
                    for name in ("input_tokens", "output_tokens", "retry_count")
                    if name in result
                }
            )
