from __future__ import annotations

import os
from datetime import date, timedelta
from io import BytesIO

import pandas as pd
import streamlit as st

from ai_enrichment import GROQ_MODELS, enrich_with_groq
from auth import logout, require_login
from funding_scraper import (
    Opportunity,
    SUPPORT_TYPES,
    SOURCE_URLS,
    filter_opportunities,
    fetch_all_sources,
    records_to_dataframe,
)


st.set_page_config(
    page_title="Funding Opportunity Finder",
    page_icon="🔎",
    layout="wide",
)

require_login()

st.title("Funding Opportunity Finder")
st.caption(
    "Find research grants, bursaries, transport support and travel allowances from "
    "the British Academy, NIHR, Wellcome, UKRI and SSA."
)


def configured_groq_key() -> str:
    if os.getenv("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"]
    try:
        return str(st.secrets.get("GROQ_API_KEY", ""))
    except Exception:
        return ""

with st.sidebar:
    st.header("NARA-CAMHR")
    st.caption("Signed in as NARA-CAMHR@hull.ac.uk")
    if st.button("Log out", use_container_width=True):
        logout()
    st.divider()
    st.header("Search settings")
    # Populate sources from the scraper configuration so a new official
    # funder cannot be silently omitted from the interface.
    sources = st.multiselect(
        "Funding sources",
        list(SOURCE_URLS),
        default=list(SOURCE_URLS),
    )
    topics = st.multiselect(
        "Research topics",
        ["Alcohol", "Addiction", "Mental health", "Related health"],
        default=["Alcohol", "Addiction", "Mental health", "Related health"],
    )
    funding_types = st.multiselect(
        "Funding support type",
        list(SUPPORT_TYPES),
        default=[],
        placeholder="All funding types",
        help="Leave empty for all types, or select transport, bursary and/or travel support.",
    )
    support_arrangement = st.selectbox(
        "Support arrangement",
        ["Any arrangement", "Dedicated awards only", "Costs within larger grants only"],
        help="Separate individual travel/bursary awards from travel costs allowed under larger research grants.",
    )
    include_general_support = st.checkbox(
        "Include general travel and bursary funding",
        value=True,
        help=(
            "Shows a relevant travel/bursary award even when alcohol, addiction "
            "or mental health is not explicitly named. Always check the funder's eligibility."
        ),
    )
    custom_query = st.text_input(
        "Additional search terms",
        placeholder="e.g. gambling, substance misuse, suicide",
        help="Separate multiple terms with commas.",
    )
    include_broad = st.checkbox(
        "Include broad/remit-based calls",
        value=True,
        help=(
            "Includes researcher-led or all-topic opportunities that may accept an "
            "alcohol, addiction or mental-health proposal even when those words are "
            "not in the title."
        ),
    )
    statuses = st.multiselect(
        "Status",
        ["Open", "Closing soon", "Opening soon", "Closed", "Unknown"],
        default=["Open", "Closing soon", "Opening soon", "Unknown", "Closed"],
    )
    date_filter = st.selectbox(
        "Date filter",
        [
            "Any date",
            "Opened in the last 7 days",
            "Opened in the last 3 months",
            "Deadline in the next 7 days",
            "Deadline in the next 3 months",
            "Custom opening-date range",
            "Custom deadline range",
        ],
    )
    custom_start = custom_end = None
    if date_filter.startswith("Custom"):
        custom_dates = st.date_input(
            "Date range",
            value=(date.today() - timedelta(days=90), date.today()),
        )
        if isinstance(custom_dates, tuple) and len(custom_dates) == 2:
            custom_start, custom_end = custom_dates

    st.caption(
        "All five funders are checked for relevant grants and support. Specialist schemes cover "
        "NIHR training, British Academy ECR Network, UKRI travel awards, Wellcome career "
        "grants and SSA programmes. Travel costs within a grant are not a standalone award."
    )
    max_pages = st.slider(
        "Pages to inspect per source",
        min_value=1,
        max_value=10,
        value=3,
        help="More pages find more calls but take longer and make more web requests.",
    )
    enrich_details = st.checkbox(
        "Read opportunity detail pages",
        value=True,
        help="Improves topic matching and may add amount, eligibility and programme details.",
    )
    st.divider()
    st.subheader("Groq AI (optional)")
    use_groq = st.checkbox(
        "Use GPT-OSS relevance review",
        value=bool(configured_groq_key()),
        help="Adds a concise AI summary and relevance assessment. Source dates and amounts remain unchanged.",
    )
    groq_model = st.selectbox("Groq model", GROQ_MODELS, disabled=not use_groq)
    groq_api_key = st.text_input(
        "Groq API key",
        value=configured_groq_key(),
        type="password",
        disabled=not use_groq,
        help="Prefer GROQ_API_KEY in your environment or Streamlit secrets. The key is not saved by this app.",
    )
    max_ai_items = st.slider(
        "Maximum opportunities for AI review",
        min_value=8,
        max_value=48,
        value=24,
        step=8,
        disabled=not use_groq,
        help="A lower value is faster and uses fewer Groq tokens.",
    )
    use_ai_relevance = st.checkbox(
        "Exclude opportunities AI marks irrelevant",
        value=False,
        disabled=not use_groq,
        help="Unassessed opportunities remain visible.",
    )

fetch_clicked = st.button("Fetch funding opportunities", type="primary", use_container_width=True)

if "opportunities" not in st.session_state:
    st.session_state.opportunities = []
if "fetch_errors" not in st.session_state:
    st.session_state.fetch_errors = []

if fetch_clicked:
    if not sources:
        st.warning("Select at least one funding source.")
    else:
        with st.spinner("Checking the selected funding websites..."):
            opportunities, errors = fetch_all_sources(
                tuple(sources),
                max_pages=max_pages,
                enrich_details=enrich_details,
            )
            if use_groq:
                opportunities, ai_errors = enrich_with_groq(
                    opportunities,
                    api_key=groq_api_key,
                    model=groq_model,
                    topics=topics,
                    custom_query=custom_query,
                    max_items=max_ai_items,
                )
                errors.extend(ai_errors)
            st.session_state.opportunities = opportunities
            st.session_state.fetch_errors = errors

if st.session_state.fetch_errors:
    with st.expander("Source warnings", expanded=False):
        for error in st.session_state.fetch_errors:
            st.warning(error)

if st.session_state.opportunities:
    filtered = filter_opportunities(
        st.session_state.opportunities,
        topics=topics,
        custom_query=custom_query,
        include_broad=include_broad,
        statuses=statuses,
        date_filter=date_filter,
        custom_start=custom_start,
        custom_end=custom_end,
        use_ai_relevance=use_ai_relevance,
        funding_types=funding_types,
        include_general_support=include_general_support,
        support_arrangement=support_arrangement,
    )
    df = records_to_dataframe(filtered)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Fetched", len(st.session_state.opportunities))
    m2.metric("Matching", len(filtered))
    m3.metric("Open / closing soon", sum(x.status in {"Open", "Closing soon"} for x in filtered))
    m4.metric("Support-related", sum(bool(item.funding_types) for item in filtered))

    st.caption(
        "Funding categories are identified from funder descriptions. The Support arrangement "
        "column distinguishes dedicated awards from expenses within wider grants. "
        "A zero count is not proof that a funder never offers that support."
    )

    # Show coverage for EVERY selected funding body, even when no support award
    # has been discovered. Counts use actual fetched listings, not assumptions.
    coverage_rows = []
    for funder in sources:
        items = [item for item in st.session_state.opportunities if item.source == funder]
        coverage_rows.append({
            "Funding body": funder,
            "Fetched": len(items),
            "Transport": sum("Transport funding" in x.funding_types for x in items),
            "Bursaries": sum("Bursaries" in x.funding_types for x in items),
            "Travel allowance": sum("Travel allowances" in x.funding_types for x in items),
            "Dedicated awards": sum(x.support_basis.startswith("Dedicated") for x in items),
        })
    with st.expander("Funding support coverage by source", expanded=False):
        st.dataframe(pd.DataFrame(coverage_rows), hide_index=True, use_container_width=True)
        st.caption(
            "Counts are from this fetch only. A source with zero matches may have unlisted "
            "programmes, restricted grants or pages that could not be retrieved."
        )
    if df.empty:
        st.info(
            "No opportunities matched these filters. Try enabling broad calls, "
            "selecting more topics or choosing ‘Any date’."
        )
    else:
        st.dataframe(
            df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "URL": st.column_config.LinkColumn("Opportunity link", display_text="Open"),
                "Relevance": st.column_config.NumberColumn("Relevance", format="%d"),
            },
        )

        csv_bytes = df.to_csv(index=False).encode("utf-8")
        excel_buffer = BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Funding opportunities")
            sheet = writer.sheets["Funding opportunities"]
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            widths = {
                "A": 22, "B": 48, "C": 18, "D": 17, "E": 17,
                "F": 28, "G": 28, "H": 34, "I": 58, "J": 26,
                "K": 28, "L": 18, "M": 55, "N": 55, "O": 18,
                "P": 40, "Q": 36, "R": 12, "S": 18,
            }
            for column, width in widths.items():
                sheet.column_dimensions[column].width = width

        c1, c2 = st.columns(2)
        c1.download_button(
            "Download CSV",
            data=csv_bytes,
            file_name="funding_opportunities.csv",
            mime="text/csv",
            use_container_width=True,
        )
        c2.download_button(
            "Download Excel",
            data=excel_buffer.getvalue(),
            file_name="funding_opportunities.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    st.caption(
        "Date filters use the opening date or deadline shown by the funder. "
        "Calls with a missing date are excluded only when a date filter is active. "
        "Always confirm eligibility and deadlines on the original funder page."
    )
else:
    st.info("Choose your filters, then click **Fetch funding opportunities**.")
