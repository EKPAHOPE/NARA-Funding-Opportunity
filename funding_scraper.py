from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup, Tag
from dateutil import parser as date_parser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BRITISH_ACADEMY_URL = "https://www.thebritishacademy.ac.uk/funding/"
NIHR_URL = "https://www.nihr.ac.uk/funding-opportunities"
WELLCOME_URL = "https://wellcome.org/research-funding/schemes"
UKRI_URL = "https://www.ukri.org/opportunity/"
SSA_URL = "https://www.addiction-ssa.org/funding/"

SOURCE_URLS = {
    "The British Academy": BRITISH_ACADEMY_URL,
    "NIHR": NIHR_URL,
    "Wellcome": WELLCOME_URL,
    "UKRI": UKRI_URL,
    "SSA": SSA_URL,
}

FUNDER_NAMES = {
    "The British Academy": "The British Academy",
    "NIHR": "National Institute for Health and Care Research",
    "Wellcome": "Wellcome",
    "UKRI": "UK Research and Innovation",
    "SSA": "Society for the Study of Addiction",
}

ALLOWED_HOSTS = {
    "www.thebritishacademy.ac.uk",
    "thebritishacademy.ac.uk",
    "www.nihr.ac.uk",
    "nihr.ac.uk",
    "www.wellcome.org",
    "wellcome.org",
    "www.ukri.org",
    "ukri.org",
    "www.addiction-ssa.org",
    "addiction-ssa.org",
}

TOPIC_PATTERNS = {
    "Alcohol": [
        r"\balcohol(?:ism|ic|-related)?\b",
        r"\balcohol use disorder\b",
        r"\bharmful drinking\b",
        r"\bbinge drinking\b",
    ],
    "Addiction": [
        r"\baddict(?:ion|ive|ed)?\b",
        r"\bsubstance (?:use|misuse|abuse)\b",
        r"\bdrug (?:use|misuse|abuse|dependence)\b",
        r"\bopioids?\b", r"\bopiates?\b", r"\bcocaine\b", r"\bcannabis\b",
        r"\btobacco\b", r"\bnicotine\b", r"\bsmoking cessation\b",
        r"\bgambl(?:ing|ing disorder)\b", r"\bbetting\b",
    ],
    "Mental health": [
        r"\bmental health\b", r"\bmental illness\b", r"\bdepress(?:ion|ive)\b",
        r"\banxiety\b", r"\bpsychosis\b", r"\bschizophrenia\b",
        r"\bsuicid(?:e|al|ality)\b", r"\bself[- ]harm\b", r"\bptsd\b",
        r"\bpost[- ]traumatic stress\b", r"\beating disorders?\b",
    ],
    "Related health": [
        r"\bbehaviou?ral health\b", r"\bpublic health\b", r"\bwell[- ]?being\b",
        r"\bhealth inequalities\b", r"\bsocial care\b", r"\bprevention\b",
        r"\btrauma\b", r"\bco-?morbid(?:ity|ities)\b",
    ],
}

# Verified SSA programme URLs. Some cards on its directory wrap their
# headings in non-standard anchors, so these also provide safe fallbacks.
# Programmes can be closed while remaining listed on the funding directory.
SSA_SCHEMES = {
    "bursaries": "/funding/flagship-research-leadership-programme/bursaries/",
    "pre-doctoral support": "/funding/flagship-research-leadership-programme/pre-doctoral-support/",
    "phd studentships": "/funding/phd-studentships/",
    "post-doctoral support": "/funding/post-doctoral-development/",
    "fellowships": "/funding/fellowships/",
    "travelling scholarships": "/funding/travelling-scholarships/",
    "engagement grants": "/funding/engagement-grants/",
    "early career researcher prize": "/funding/fred-yates-prize-for-early-career-researchers/",
    "impact prize": "/funding/impact-prize/",
    # The events / special-interest-groups programme is included only when
    # a valid link is actually present on the SSA directory.
    "special interest groups and events": None,
}

SSA_SUMMARIES = {
    "bursaries": "Funding for postgraduate study and professional development in addiction research.",
    "pre-doctoral support": "Salary support for developing a PhD application in addiction science.",
    "phd studentships": "Support to design and conduct doctoral research in addiction science.",
    "post-doctoral support": "Salary support while preparing postdoctoral fellowship applications.",
    "fellowships": "Funding for a three-year postdoctoral programme in addiction research.",
    "travelling scholarships": "International visits and networking for addiction researchers.",
    "engagement grants": "Initiatives improving understanding of addiction and treatment.",
    "early career researcher prize": "Annual award recognising early-career contributions to addiction science.",
    "impact prize": "Award recognising public-facing impact of addiction research.",
    "special interest groups and events": "Support for collaborative events and special interest groups in addiction science.",
}


BROAD_PATTERNS = [
    r"\bresearcher[- ]led\b",
    r"\ball research topics\b",
    r"\bresponsive (?:call|mode|funding)\b",
    r"\brolling funding opportunity\b",
    r"\bcuriosity[- ]driven research\b",
    r"\binterdisciplinary research\b",
]


@dataclass
class Opportunity:
    source: str
    funder: str
    title: str
    url: str
    summary: str = ""
    opening_date: date | None = None
    deadline: date | None = None
    status: str = "Unknown"
    programme: str = ""
    amount: str = ""
    eligibility: str = ""
    matched_topics: tuple[str, ...] = ()
    matched_keywords: tuple[str, ...] = ()
    broad_call: bool = False
    relevance_score: int = 0
    ai_relevant: bool | None = None
    ai_summary: str = ""
    ai_reason: str = ""


def _session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
        }
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _get_html(session: requests.Session, url: str, params: dict | None = None) -> str:
    host = urlparse(url).hostname
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"Unsupported host: {host}")
    response = session.get(url, params=params, timeout=(8, 30))
    response.raise_for_status()
    if "html" not in response.headers.get("Content-Type", "html").lower():
        raise ValueError(f"Expected HTML from {url}")
    return response.text


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date_parser.parse(value, dayfirst=True, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _labelled_date(text: str, labels: Iterable[str]) -> date | None:
    month = r"January|February|March|April|May|June|July|August|September|October|November|December"
    for label in labels:
        match = re.search(
            rf"{label}\s*:?\s*(\d{{1,2}}\s+(?:{month})\s+\d{{4}})",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return _parse_date(match.group(1))
    return None


def _normalise_status(raw: str, deadline: date | None) -> str:
    value = raw.lower()
    today = date.today()
    if "opening soon" in value or "upcoming" in value:
        return "Opening soon"
    if "closing soon" in value:
        return "Closing soon"
    if re.search(r"\bclosed\b", value):
        return "Closed"
    if re.search(r"\bopen\b", value):
        if deadline and today <= deadline <= today + timedelta(days=14):
            return "Closing soon"
        return "Open"
    if deadline:
        if deadline < today:
            return "Closed"
        if deadline <= today + timedelta(days=14):
            return "Closing soon"
        return "Open"
    return "Unknown"


def _smallest_opportunity_container(heading: Tag, source: str) -> Tag | None:
    current: Tag | None = heading
    for _ in range(7):
        current = current.parent if isinstance(current, Tag) else None
        if current is None:
            return None
        text = _clean(current.get_text(" ", strip=True)).lower()
        links = current.find_all("a", href=True)
        has_funding_link = any(_valid_funding_href(a.get("href", ""), source) for a in links)
        date_or_status_labels = (
            "closing date",
            "deadline",
            "applications close",
            "apply anytime",
            "frequency",
            "opportunity status",
            "opening date",
        )
        if has_funding_link and any(label in text for label in date_or_status_labels):
            return current
    return None


def _valid_funding_href(href: str, source: str) -> bool:
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "javascript:")):
        return False
    base_url = SOURCE_URLS.get(source, href)
    path = urlparse(urljoin(base_url, href)).path.rstrip("/")
    if source == "SSA":
        parsed = urlparse(urljoin(base_url, href))
        return (
            parsed.hostname in {"addiction-ssa.org", "www.addiction-ssa.org"}
            and path.startswith("/funding/")
            and path != "/funding"
        )
    if source == "NIHR":
        return path.startswith("/funding/") and path.count("/") >= 2
    if source == "UKRI":
        return path.startswith("/opportunity/") and path != "/opportunity"
    if source == "Wellcome":
        wellcome_prefixes = (
            "/research-funding/schemes/",
            "/grant-funding/schemes/",
        )
        return any(path.startswith(prefix) for prefix in wellcome_prefixes)
    excluded = {"/funding", "/funding/guidance", "/funding/assessment-and-peer-review"}
    return path.startswith("/funding/") and path not in excluded


def _find_url(heading: Tag, container: Tag, base_url: str, source: str) -> str:
    candidates = []
    if heading.name == "a" and heading.get("href"):
        candidates.append(heading)
    candidates.extend(heading.find_all("a", href=True))
    candidates.extend(container.find_all("a", href=True))
    for anchor in candidates:
        href = anchor.get("href", "")
        if _valid_funding_href(href, source):
            return urljoin(base_url, href)
    return base_url


def _first_summary(container: Tag, title: str) -> str:
    for paragraph in container.find_all("p"):
        text = _clean(paragraph.get_text(" ", strip=True))
        lower = text.lower()
        if len(text) >= 35 and text != title and not lower.startswith(("opening date", "closing date", "status")):
            return text[:700]
    return ""


def _extract_amount(text: str) -> str:
    patterns = [
        r"(?:up to|maximum(?: award)?(?: of)?|value(?: of)?|award(?: of)?)\s*(£\s?\d[\d,]*(?:\.\d{2})?)",
        r"(£\s?\d[\d,]*(?:\.\d{2})?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _clean(match.group(1)).replace("£ ", "£")
    return ""


def _section_text(soup: BeautifulSoup, heading_pattern: str, max_chars: int = 500) -> str:
    heading = soup.find(re.compile(r"^h[1-6]$"), string=re.compile(heading_pattern, re.I))
    if not heading:
        return ""
    parts = []
    for sibling in heading.find_all_next():
        if sibling is not heading and sibling.name and re.fullmatch(r"h[1-6]", sibling.name):
            break
        if sibling.name in {"p", "li"}:
            value = _clean(sibling.get_text(" ", strip=True))
            if value:
                parts.append(value)
        if len(" ".join(parts)) >= max_chars:
            break
    return " ".join(parts)[:max_chars]


def parse_ssa_listing(html: str, base_url: str = SSA_URL) -> list[Opportunity]:
    """Read SSA funding *schemes*, not arbitrary articles or site navigation.

    SSA lists permanent programmes rather than a conventional paginated calls
    feed. Their current opening status is determined by each programme page.
    """
    soup = BeautifulSoup(html, "html.parser")
    for unwanted in soup(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        unwanted.decompose()
    main = soup.find("main") or soup
    seen = set()
    opportunities = []
    for heading in main.find_all(["h2", "h3", "h4", "h5"]):
        title = _clean(heading.get_text(" ", strip=True))
        key = title.casefold()
        if key not in SSA_SCHEMES:
            continue

        # A linked card can wrap the entire heading; other designs link the
        # heading text itself or put the link in the immediate card container.
        anchors = []
        parent_anchor = heading.find_parent("a", href=True)
        if parent_anchor is not None:
            anchors.append(parent_anchor)
        anchors.extend(heading.find_all("a", href=True))
        card = heading.parent
        for _ in range(3):
            if isinstance(card, Tag):
                anchors.extend(card.find_all("a", href=True, limit=6))
                card = card.parent
        linked_url = next(
            (urljoin(base_url, a["href"]) for a in anchors if _valid_funding_href(a["href"], "SSA")),
            None,
        )
        canonical = SSA_SCHEMES[key]
        url = urljoin(base_url, canonical) if canonical else linked_url
        if not url or url in seen:
            continue
        seen.add(url)
        summary = SSA_SUMMARIES[key]
        # Preserve the scheme's own short description from the directory.
        sibling = heading.find_next(["p", "h2", "h3", "h4", "h5"])
        if sibling is not None and sibling.name == "p":
            candidate = _clean(sibling.get_text(" ", strip=True))
            if 12 <= len(candidate) <= 400:
                summary = candidate
        opportunity = Opportunity(
            source="SSA",
            funder=FUNDER_NAMES["SSA"],
            title=title,
            url=url,
            summary=summary,
            programme="SSA funding and prizes",
            status="Unknown",  # Filled from the current scheme detail page.
        )
        classify_opportunity(opportunity)
        opportunities.append(opportunity)
    return opportunities


def _ssa_deadline(text: str) -> date | None:
    labelled = _labelled_date(text, ["closing date", "deadline", "applications close"])
    if labelled:
        return labelled
    month = r"January|February|March|April|May|June|July|August|September|October|November|December"
    for pattern in (
        rf"\b(?:closed|close|closes|closing)\s+(?:on|at)\s+(\d{{1,2}}\s+(?:{month})\s+\d{{4}})",
        rf"\buntil\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*(\d{{1,2}}\s+(?:{month})\s+\d{{4}})",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            return _parse_date(match.group(1))
    return None


def _ssa_status(lead: str, deadline: date | None) -> str:
    """Use *current round* notices, avoiding historical open dates as status."""
    today = date.today()
    text = lead.lower()
    if re.search(r"(?:applications? (?:are |have )?(?:currently |now )?closed|current round.{0,90}closed|"
                 r"applications? closed|scheme (?:is )?under review|funding (?:is )?fully allocated|"
                 r"all available funding.{0,40}allocated|applications? for the 20\d{2} prize.{0,35}closed)", text):
        return "Closed"
    if deadline and deadline < today:
        return "Closed"
    if re.search(r"(?:will open (?:for applications )?soon|opening soon|open for applications soon)", text):
        return "Opening soon"
    if re.search(r"(?:applications? (?:are |is )?(?:now |currently )?open|accepting applications|"
                 r"apply now|applications will remain open)", text):
        return "Closing soon" if deadline and deadline <= today + timedelta(days=14) else "Open"
    if deadline:
        return "Closing soon" if deadline <= today + timedelta(days=14) else "Unknown"
    return "Unknown"


def enrich_ssa_opportunity(opportunity: Opportunity, html: str) -> Opportunity:
    """Extract status, eligibility and amounts from SSA's individual scheme page."""
    soup = BeautifulSoup(html, "html.parser")
    for unwanted in soup(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        unwanted.decompose()
    main = soup.find("main") or soup.find("article") or soup
    paragraphs = [
        _clean(p.get_text(" ", strip=True)) for p in main.find_all("p")
        if len(_clean(p.get_text(" ", strip=True))) >= 20
    ]
    # Initial notices describe the CURRENT application round. Later sections
    # may contain historical closing dates, so don't infer from the full page.
    lead = " ".join(paragraphs[:6])[:1700]
    deadline = _ssa_deadline(lead)
    opportunity.deadline = deadline
    opportunity.status = _ssa_status(lead, deadline)
    if paragraphs and len(paragraphs[0]) >= 45:
        opportunity.summary = paragraphs[0][:700]
    opportunity.amount = _extract_amount(" ".join(paragraphs[:25]))
    opportunity.eligibility = _section_text(
        soup, r"^(?:eligibility|who can apply|am i eligible|criteria)"
    )
    classify_opportunity(opportunity, extra_text=" ".join(paragraphs[:25])[:5000])
    return opportunity


def parse_listing(html: str, base_url: str, source: str) -> list[Opportunity]:
    if source == "SSA":
        return parse_ssa_listing(html, base_url)
    soup = BeautifulSoup(html, "html.parser")
    for unwanted in soup(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        unwanted.decompose()
    opportunities: list[Opportunity] = []
    seen: set[str] = set()

    for heading in soup.find_all(["h2", "h3", "h4"]):
        title = _clean(heading.get_text(" ", strip=True))
        if len(title) < 5 or title.lower() in {"funding", "funding opportunities", "open for applications"}:
            continue
        container = _smallest_opportunity_container(heading, source)
        if container is None:
            continue
        url = _find_url(heading, container, base_url, source)
        if url == base_url or url in seen:
            continue
        seen.add(url)
        text = _clean(container.get_text(" ", strip=True))
        opening = _labelled_date(text, ["opening date", "open date", "applications open"])
        deadline = _labelled_date(text, ["closing date", "deadline", "applications close"])
        status_match = re.search(
            r"(?:status|opportunity status)\s*:?\s*(upcoming|opening soon|closing soon|open|closed)",
            text,
            re.I,
        )
        status = _normalise_status(status_match.group(1) if status_match else "", deadline)
        opportunities.append(
            Opportunity(
                source=source,
                funder=FUNDER_NAMES.get(source, source),
                title=title,
                url=url,
                summary=_first_summary(container, title),
                opening_date=opening,
                deadline=deadline,
                status=status,
                amount=_extract_amount(text),
            )
        )
    return opportunities


def enrich_opportunity(opportunity: Opportunity, html: str) -> Opportunity:
    if opportunity.source == "SSA":
        return enrich_ssa_opportunity(opportunity, html)
    soup = BeautifulSoup(html, "html.parser")
    for unwanted in soup(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        unwanted.decompose()
    main = soup.find("main") or soup.find("article") or soup
    text = _clean(main.get_text(" ", strip=True))
    title_tag = main.find("h1")
    if title_tag:
        opportunity.title = _clean(title_tag.get_text(" ", strip=True)) or opportunity.title
    opportunity.opening_date = opportunity.opening_date or _labelled_date(
        text, ["opening date", "open date", "applications open", "earliest start date"]
    )
    opportunity.deadline = opportunity.deadline or _labelled_date(
        text, ["closing date", "deadline date", "deadline", "applications close"]
    )
    opportunity.status = _normalise_status(text[:1000], opportunity.deadline)
    opportunity.amount = opportunity.amount or _extract_amount(text)
    eligibility = _section_text(soup, r"^(eligibility|who can apply)")
    if eligibility:
        opportunity.eligibility = eligibility
    programme_match = re.search(r"(?:Type|Funding programme)\s*:?\s*([A-Za-z][A-Za-z &/-]{2,80})", text)
    if programme_match:
        opportunity.programme = _clean(programme_match.group(1))
    paragraphs = main.find_all("p")
    for paragraph in paragraphs:
        candidate = _clean(paragraph.get_text(" ", strip=True))
        if len(candidate) >= 50 and not candidate.lower().startswith(("cookie", "we use cookies")):
            opportunity.summary = opportunity.summary or candidate[:700]
            break
    classify_opportunity(opportunity, extra_text=text)
    return opportunity


def classify_opportunity(opportunity: Opportunity, extra_text: str = "") -> Opportunity:
    haystack = " ".join(
        [opportunity.title, opportunity.summary, opportunity.programme, opportunity.eligibility, extra_text]
    ).lower()
    matched_topics = []
    matched_keywords = []
    for topic, patterns in TOPIC_PATTERNS.items():
        topic_hits = []
        for pattern in patterns:
            match = re.search(pattern, haystack, flags=re.I)
            if match:
                topic_hits.append(_clean(match.group(0)).lower())
        if topic_hits:
            matched_topics.append(topic)
            matched_keywords.extend(topic_hits)
    broad = any(re.search(pattern, haystack, flags=re.I) for pattern in BROAD_PATTERNS)
    if opportunity.source == "SSA" and "Addiction" not in matched_topics:
        matched_topics.append("Addiction")
    opportunity.matched_topics = tuple(dict.fromkeys(matched_topics))
    opportunity.matched_keywords = tuple(dict.fromkeys(matched_keywords))
    opportunity.broad_call = broad
    opportunity.relevance_score = min(100, len(opportunity.matched_topics) * 25 + len(opportunity.matched_keywords) * 5 + (10 if broad else 0))
    return opportunity


def _fetch_source(source: str, max_pages: int, enrich_details: bool) -> tuple[list[Opportunity], list[str]]:
    if source not in SOURCE_URLS:
        return [], [f"Unsupported funding source: {source}"]
    base_url = SOURCE_URLS[source]
    session = _session()
    opportunities: list[Opportunity] = []
    errors: list[str] = []
    known_urls: set[str] = set()

    # SSA has one programme directory; each programme supplies its own status.
    page_count = 1 if source == "SSA" else max_pages
    for page_number in range(1, page_count + 1):
        try:
            if source == "SSA":
                params = None
            elif source in {"NIHR", "UKRI", "Wellcome"}:
                params = {"page": page_number} if page_number > 1 else None
            else:
                params = {"page": page_number, "order": "-last_published_at"} if page_number > 1 else None
            html = _get_html(session, base_url, params=params)
            page_items = parse_listing(html, base_url, source)
            new_items = [item for item in page_items if item.url not in known_urls]
            if not new_items:
                break
            for item in new_items:
                known_urls.add(item.url)
                opportunities.append(item)
        except Exception as exc:
            errors.append(f"{source}, page {page_number}: {type(exc).__name__}: {exc}")
            break

    if enrich_details:
        for index, opportunity in enumerate(opportunities[:40]):
            try:
                detail_html = _get_html(session, opportunity.url)
                opportunities[index] = enrich_opportunity(opportunity, detail_html)
                time.sleep(0.08)
            except Exception as exc:
                errors.append(f"Could not read details for ‘{opportunity.title}’: {type(exc).__name__}")
                classify_opportunity(opportunity)
    else:
        for opportunity in opportunities:
            classify_opportunity(opportunity)

    return opportunities, errors


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_all_sources(
    sources: tuple[str, ...],
    max_pages: int = 3,
    enrich_details: bool = True,
) -> tuple[list[Opportunity], list[str]]:
    all_items: list[Opportunity] = []
    errors: list[str] = []
    for source in sources:
        items, source_errors = _fetch_source(source, max_pages=max_pages, enrich_details=enrich_details)
        all_items.extend(items)
        errors.extend(source_errors)
    deduped = {item.url: item for item in all_items}
    return list(deduped.values()), errors


def _custom_terms(custom_query: str) -> list[str]:
    return [term.strip().lower() for term in custom_query.split(",") if term.strip()]


def _date_matches(
    opportunity: Opportunity,
    date_filter: str,
    custom_start: date | None,
    custom_end: date | None,
) -> bool:
    today = date.today()
    if date_filter == "Any date":
        return True
    if date_filter == "Opened in the last 7 days":
        return opportunity.opening_date is not None and today - timedelta(days=7) <= opportunity.opening_date <= today
    if date_filter == "Opened in the last 3 months":
        return opportunity.opening_date is not None and today - timedelta(days=92) <= opportunity.opening_date <= today
    if date_filter == "Deadline in the next 7 days":
        return opportunity.deadline is not None and today <= opportunity.deadline <= today + timedelta(days=7)
    if date_filter == "Deadline in the next 3 months":
        return opportunity.deadline is not None and today <= opportunity.deadline <= today + timedelta(days=92)
    if custom_start is None or custom_end is None:
        return False
    target = opportunity.opening_date if "opening-date" in date_filter else opportunity.deadline
    return target is not None and custom_start <= target <= custom_end


def filter_opportunities(
    opportunities: list[Opportunity],
    topics: list[str],
    custom_query: str,
    include_broad: bool,
    statuses: list[str],
    date_filter: str,
    custom_start: date | None = None,
    custom_end: date | None = None,
    use_ai_relevance: bool = False,
) -> list[Opportunity]:
    terms = _custom_terms(custom_query)
    results = []
    for opportunity in opportunities:
        searchable = " ".join(
            [opportunity.title, opportunity.summary, opportunity.programme, opportunity.eligibility]
        ).lower()
        selected_topic_match = bool(set(topics) & set(opportunity.matched_topics))
        custom_match = bool(terms) and any(term in searchable for term in terms)
        relevance_match = selected_topic_match or custom_match or (include_broad and opportunity.broad_call)
        if not topics and not terms:
            relevance_match = include_broad and opportunity.broad_call
        if not relevance_match:
            continue
        if use_ai_relevance and opportunity.ai_relevant is False:
            continue
        if statuses and opportunity.status not in statuses:
            continue
        if not _date_matches(opportunity, date_filter, custom_start, custom_end):
            continue
        results.append(opportunity)
    return sorted(
        results,
        key=lambda item: (
            item.relevance_score,
            item.opening_date or date.min,
            item.deadline or date.max,
        ),
        reverse=True,
    )


def records_to_dataframe(opportunities: list[Opportunity]) -> pd.DataFrame:
    rows = []
    for item in opportunities:
        rows.append(
            {
                "Source": item.source,
                "Title": item.title,
                "Status": item.status,
                "Opening date": item.opening_date.isoformat() if item.opening_date else "",
                "Deadline": item.deadline.isoformat() if item.deadline else "",
                "Programme": item.programme,
                "Matched topics": ", ".join(item.matched_topics) or ("Broad remit" if item.broad_call else ""),
                "Matched keywords": ", ".join(item.matched_keywords),
                "Amount": item.amount,
                "Summary": item.summary,
                "AI summary": item.ai_summary,
                "AI assessment": (
                    "Relevant" if item.ai_relevant is True else "Not relevant" if item.ai_relevant is False else "Not assessed"
                ),
                "AI reason": item.ai_reason,
                "Eligibility": item.eligibility,
                "Relevance": item.relevance_score,
                "URL": item.url,
            }
        )
    return pd.DataFrame(rows)
