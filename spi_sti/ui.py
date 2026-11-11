from pathlib import Path

import numpy as np
import streamlit as st
from PIL import Image

from spi_sti.auth import initialize, read_service_account
from spi_sti.zonal import guess_name_field, read_boundary

ROOT = Path(__file__).resolve().parent.parent
LOGO_DIR = ROOT / "statics" / "images"


def render_header():
    dost, pcaarrd, uplb, sarai = st.columns([1, 1, 1, 2.6], vertical_alignment="center", gap="medium")
    dost.image(_logo(LOGO_DIR.parent / "dost_logo.png", drop_white=True), width=84)
    pcaarrd.image(_logo(LOGO_DIR / "dost_pcaard.png"), width=84)
    uplb.image(_logo(LOGO_DIR / "uplbl_logo.png"), width=84)
    sarai.image(_logo(LOGO_DIR / "sarai_long.png"), width=360)
    st.caption(
        "Department of Science and Technology · DOST-PCAARRD · "
        "University of the Philippines Los Baños · Project SARAI"
    )


def render_sidebar():
    with st.sidebar:
        st.header("Earth Engine")
        st.caption("The service account needs Owner on a Cloud project that already has the Earth Engine API enabled.")
        with st.expander("How to create a service account key"):
            st.markdown(
                "Use the Google account that already has Earth Engine. Stay on the Cloud project where that API is already turned on.\n\n"
                "1. Open [Service accounts](https://console.cloud.google.com/iam-admin/serviceaccounts) and select that project.\n"
                "2. If the project has no service account yet, create one and grant it the **Owner** role.\n"
                "3. Click the service account email, then open the **Keys** tab.\n"
                "4. Click **Add key**, then **Create new key**.\n"
                "5. Choose **JSON** and click **Create**. The file downloads once. Google will not offer that same key again.\n"
                "6. Upload the JSON file here. Do not commit it or send it to anyone.\n\n"
                "If a run fails in the first minute after the key is created, wait and try again. "
                "Google's full steps, including the gcloud commands, are in "
                "[Create and delete service account keys](https://docs.cloud.google.com/iam/docs/keys-create-delete). "
                "That page also explains what to do if the project refuses to create a key. "
                "SINAG's walkthrough is "
                "[Configure Google Earth Engine API and service key](https://dostsinag.com/documentation/guides/gee-service-key/). "
                "After the JSON downloads, register its `client_email` on the "
                "[Earth Engine service accounts](https://signup.earthengine.google.com/#!/service_accounts) page."
            )
        key_file = st.file_uploader("Service account JSON", type=["json"])
        if key_file is not None:
            raw = key_file.getvalue()
            if raw != st.session_state.get("_key_raw"):
                try:
                    payload = read_service_account(raw)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["_key_raw"] = raw
                    st.session_state["key"] = payload
                    st.session_state["project"] = payload.get("project_id") or ""

        st.text_input("Google Cloud project", key="project")

        st.header("Boundary")
        st.caption("Used by the drought map, and as the municipality list on the point page.")
        shape_file = st.file_uploader("Shapefile zip", type=["zip"])
        if shape_file is not None:
            raw_shape = shape_file.getvalue()
            if raw_shape != st.session_state.get("_shape_raw"):
                try:
                    gdf, layer = read_boundary(raw_shape)
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.session_state["_shape_raw"] = raw_shape
                    st.session_state["gdf"] = gdf
                    st.session_state["layer"] = layer

        gdf = st.session_state.get("gdf")
        name_field = None
        if gdf is not None:
            fields = [col for col in gdf.columns if col != "geometry"]
            try:
                default = guess_name_field(gdf)
            except ValueError as exc:
                st.error(str(exc))
                fields = []
            if fields:
                name_field = st.selectbox(
                    "Municipality name field",
                    fields,
                    index=fields.index(default) if default in fields else 0,
                    key=f"name-field-{st.session_state.get('layer')}",
                )
                st.caption(f"{len(gdf)} polygons in {st.session_state.get('layer')}.")
        st.session_state["name_field"] = name_field


def connect():
    payload = st.session_state.get("key")
    if not payload:
        raise ValueError("Upload a service account JSON key first.")
    return initialize(payload, st.session_state.get("project"))


def explain(exc):
    text = str(exc).strip()
    lowered = text.lower()
    if "not registered" in lowered or "required permission" in lowered or "permission_denied" in lowered:
        return (
            text
            + " Use a service account that is Owner on a Cloud project registered for Earth Engine, "
            "with the Earth Engine API enabled."
        )
    return text


def _logo(path, drop_white=False):
    image = Image.open(path).convert("RGBA")
    if not drop_white:
        return image
    pixels = np.array(image)
    white = pixels[:, :, :3].min(axis=2) > 245
    pixels[white, 3] = 0
    return Image.fromarray(pixels)
