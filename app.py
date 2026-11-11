import streamlit as st

from spi_sti.pages import map_page, point_page
from spi_sti.ui import render_header, render_sidebar

st.set_page_config(page_title="SPI and STI drought tool", layout="wide")
render_header()
render_sidebar()

navigation = st.navigation(
    [
        st.Page(map_page.render, title="Drought map", icon=":material/map:", default=True),
        st.Page(
            point_page.render,
            title="Point analysis",
            icon=":material/my_location:",
            url_path="point",
        ),
    ]
)
navigation.run()
