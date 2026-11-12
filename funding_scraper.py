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

# NIHR opportunities also appear on regional and school websites. These are
# official NIHR programme pages, not general-purpose web search results.
NIHR_EXTRA_PAGES = (
    "https://sphr.nihr.ac.uk/prevention/funding-opportunities/",
    "https://cambridgebrc.nihr.ac.uk/our-research/clinical-research-at-cuh/applying-for-funding-2/funding-sources/individual-training-awards/nihr-training-awards/",
)

# All original funding bodies remain enabled. Supplementary scheme pages below
# fill gaps where a funder advertises travel support away from its main feed.
SOURCE_URLS = {
    "The British Academy": BRITISH_ACADEMY_URL,
    "NIHR": NIHR_URL,
    "Wellcome": WELLCOME_URL,
    "UKRI": UKRI_URL,
    "SSA": SSA_URL,
}

# These are official programme pages checked against the funders' websites.
# They are fetched LIVE: if a page fails, the app emits a warning instead of
# creating an unsupported funding result. Do not infer an open call from this
# catalogue; dates and status must be extracted from the live page.
FEATURED_PROGRAMMES = {
    "The British Academy": (
        {
            "title": "Early Career Researcher Network travel and development support",
            "url": "https://www.thebritishacademy.ac.uk/early-career-researcher-network/early-career-researcher-network-what-we-offer/",
            "summary": (
                "Network members can request up to £300 for relevant conferences, "
                "training, workshops and travel for professional development; "
                "event travel and accommodation support is also described."
            ),
            "eligibility": "For eligible British Academy Early Career Researcher Network members.",
            "programme": "ECR Network development fund and travel support",
            "amount": "Up to £300 (development stream)",
        },
    ),
    "NIHR": (
        {
            "title": "NIHR Pre-Application Support Fund",
            "url": "https://www.nihr.ac.uk/career-development/research-career-funding-programmes/supporting-career-development/pre-application-support-fund",
            "summary": (
                "Flexible funding to prepare an NIHR career development award, "
                "including relevant training and placements; exact travel and "
                "conference cost eligibility depends on the current award guidance."
            ),
            "eligibility": "Applicants developing an NIHR career development award.",
            "programme": "NIHR career development support",
        },
    ),
    "Wellcome": (
        {
            "title": "Wellcome Early-Career Awards",
            "url": "https://wellcome.org/research-funding/schemes/wellcome-early-career-awards",
            "summary": (
                "Research award covering justified project-related travel, "
                "conference attendance and subsistence as part of the funded research."
            ),
            "eligibility": "For eligible early-career researchers; see the official scheme rules.",
            "programme": "Early-career research award",
        },
        {
            "title": "Wellcome Career Development Awards",
            "url": "https://wellcome.org/research-funding/schemes/wellcome-career-development-awards",
            "summary": (
                "Career development award allowing justified travel, conference "
                "attendance and subsistence costs within a research project."
            ),
            "eligibility": "For eligible mid-career researchers; see the official scheme rules.",
            "programme": "Career development research award",
        },
    ),
    "UKRI": (
        {
            "title": "BBSRC International Travel Award Scheme for the Biological Sciences",
            "url": "https://www.ukri.org/opportunity/bbsrc-international-travel-award-scheme/",
            "summary": (
                "Travel and subsistence for collaborations and visits to overseas "
                "research facilities; limited to eligible BBSRC-supported researchers."
            ),
            "eligibility": "Current BBSRC grant holders or researchers at BBSRC-supported institutes; not students.",
            "programme": "BBSRC international travel award",
            "amount": "Up to £3,000",
        },
        {
            "title": "EPSRC Overseas Travel Grant",
            "url": "https://www.ukri.org/opportunity/epsrc-overseas-travel-grant/",
            "summary": (
                "Supports international travel and subsistence to learn techniques "
                "or develop collaborations, but not solely conference attendance."
            ),
            "eligibility": "Researchers and institutions eligible under EPSRC rules.",
            "programme": "EPSRC overseas travel grant",
        },
    ),
    # SSA is fully covered through its funding directory and individual scheme
    # pages; do not duplicate its travelling scholarship or bursary listings.
    "SSA": (),
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
    "sphr.nihr.ac.uk",
    "cambridgebrc.nihr.ac.uk",
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

# Funding support is separate from research topic: general travel support
# may be useful to NARA researchers even when addiction is not in its title.
SUPPORT_TYPES = ("Transport funding", "Bursaries", "Travel allowances")
SUPPORT_PATTERNS = {
    "Transport funding": [
        r"\btransport(?:ation)?\s+(?:costs?|expenses?|funding|allowance|support)\b",
        r"\btravel\s+(?:costs?|expenses?|reimbursements?|funding|support)\b",
        r"\b(?:rail|train|bus|flight|airfare|mileage)\s+(?:costs?|expenses?|reimbursement)\b",
        r"\b(?:travel|transport)\s+(?:and|&)\s+(?:subsistence|accommodation)\b",
        r"\btravell?ing\s+(?:scholarship|bursary|grant)\b",
        r"\btravel\s+(?:award|grant|bursary|scholarship|support|funding)\b",
    ],
    "Bursaries": [r"\bbursar(?:y|ies)\b"],
    "Travel allowances": [
        r"\btravel\s+(?:allowance|grant|bursary|scholarship|stipend|expenses?|costs?|support|funding)s?\b",
        r"\binternational travel awards?\b",
        r"\boverseas travel grants?\b",
        r"\btravell?ing\s+scholarships?\b",
        r"\btravel\s+(?:and|&)\s+(?:subsistence|accommodation)\b",
        r"\b(?:conference|research|fieldwork)\s+(?:travel|attendance)\s+(?:funding|support|costs?|expenses?)\b",
        r"\bconference\s+(?:costs?|fees?)\s+(?:including|and)\s+travel\b",
    ],
}
# A plain "travel" mention, or an exclusion of standalone travel, is NOT an award.
NEGATIVE_SUPPORT_PATTERNS = (
    r"(?:do not|don't|does not|doesn't|will not|won't|cannot|can't|not)\s+(?:usually\s+)?fund\s+(?:standalone\s+)?(?:travel|conference|transport)",
    r"\b(?:travel|conference|transport)(?:\s+and\s+conference)?\s+(?:costs?|expenses?|fees?)\s+(?:are\s+)?(?:not eligible|not funded|excluded)",
)
DEDICATED_PATTERNS = (
    r"\bbursar(?:y|ies)\b",
    r"\b(?:travel|travelling|transport|overseas travel|international travel)\s+(?:award|grant|scholarship|bursary|allowance|fund|funding|support|scheme)\b",
)

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
    funding_types: tuple[str, ...] = ()
    support_basis: str = "Not specified"
    support_evidence: str = ""


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


def _canonical_url(url: str) -> str:
    """Normalise only host, trailing slash and fragments for safe de-duplication.

    A page returned through a directory may have a trailing slash while a
    curated direct-scheme URL does not. They refer to the same opportunity.
    Query parameters stay intact because some sites distinguish their calls
    with those parameters.
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(netloc=parsed.netloc.lower(), path=path, fragment="").geturl()


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
        return (
            (path.startswith("/funding/") and path.count("/") >= 2)
            or path.startswith("/funding-opportunities/")
            or path.startswith("/career-development/research-career-funding-programmes/")
        )
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


def parse_nihr_specialist_listing(html: str, base_url: str) -> list[Opportunity]:
    """Read NIHR Three Schools calls and Cambridge BRC's NIHR award guide.

    For the Cambridge guide we report opportunities with status Unknown because
    a description of a funding programme is not evidence a round is open.
    """
    soup = BeautifulSoup(html, "html.parser")
    for unwanted in soup(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        unwanted.decompose()
    main = soup.find("main") or soup.find("article") or soup
    seen: set[str] = set()
    items = []
    is_cambridge = "cambridgebrc.nihr.ac.uk" in urlparse(base_url).netloc
    headings = main.find_all(["h2"] if is_cambridge else ["h3", "h4"])

    for heading in headings:
        title = _clean(heading.get_text(" ", strip=True))
        if len(title) < 9 or len(title) > 120:
            continue
        if re.search(r"(?:latest news|more information|other funding|related content|you may also)", title, re.I):
            continue
        if is_cambridge and not re.search(r"award|fund|fellowship|lectureship|support", title, re.I):
            continue

        # Collect text and links only until the next heading at the same level.
        chunks = []
        anchors = []
        for item in heading.next_elements:
            if item is heading:
                continue
            if isinstance(item, Tag) and item.name == heading.name:
                break
            if isinstance(item, Tag) and item.name in {"p", "li"}:
                part = _clean(item.get_text(" ", strip=True))
                if part and (not chunks or part != chunks[-1]):
                    chunks.append(part)
            if isinstance(item, Tag) and item.name == "a" and item.get("href"):
                anchors.append(item)
            if len(" ".join(chunks)) > 1800:
                break
        text = _clean(" ".join(chunks))[:1800]
        if not text or (not is_cambridge and not re.search(r"fund|award|grant|application|placement|fellowship|bursar", title + " " + text, re.I)):
            continue
        valid_links = [urljoin(base_url, a["href"]) for a in anchors
                       if urlparse(urljoin(base_url, a["href"])).hostname in ALLOWED_HOSTS]
        url = next((link for link in valid_links if re.search(r"fund|award|research|career|support|placement", urlparse(link).path, re.I)),
                   valid_links[0] if valid_links else base_url)
        # Distinct page anchors are used only as a last resort for guide entries.
        unique_key = title.casefold() if is_cambridge else url
        if unique_key in seen:
            continue
        seen.add(unique_key)
        deadline = _labelled_date(text, ["deadline for applications is(?: noon)?(?: friday)?", "closing date", "deadline", "applications close"])
        if not deadline:
            month = r"January|February|March|April|May|June|July|August|September|October|November|December"
            match = re.search(rf"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*(\d{{1,2}})(?:st|nd|rd|th)?\s+({month})\s+(20\d{{2}})", text, re.I)
            if match:
                deadline = _parse_date(" ".join(match.groups()))
        status = _normalise_status("", deadline) if deadline else "Unknown"
        opportunity = Opportunity("NIHR", FUNDER_NAMES["NIHR"], title, url,
                                  summary=text[:700], programme="NIHR regional / school funding",
                                  deadline=deadline, status=status, amount=_extract_amount(text))
        classify_opportunity(opportunity, extra_text=text)
        items.append(opportunity)
    return items


def parse_listing(html: str, base_url: str, source: str) -> list[Opportunity]:
    if source == "SSA":
        return parse_ssa_listing(html, base_url)
    soup = BeautifulSoup(html, "html.parser")
    for unwanted in soup(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        unwanted.decompose()
    opportunities: list[Opportunity] = []
    seen: set[str] = set()

    # Scheme cards can say "Apply anytime" without opening/closing date labels.
    for heading in soup.find_all(["h2", "h3", "h4"]):
        title = _clean(heading.get_text(" ", strip=True))
        if len(title) < 5 or title.lower() in {"funding", "funding opportunities", "open for applications"}:
            continue
        container = _smallest_opportunity_container(heading, source)
        if container is None and source == "Wellcome":
            anchor = heading.find("a", href=True) or heading.find_parent("a", href=True)
            if anchor is not None and _valid_funding_href(anchor.get("href", ""), source):
                # A link to a genuine scheme is sufficient; date labels are optional.
                container = heading.parent if isinstance(heading.parent, Tag) else heading
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
        if source == "Wellcome" and re.search(r"apply anytime|applications accepted year.round", text, re.I):
            status = "Open"
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
    status_match = re.search(
        r"\b(?:opportunity|funding) status\s*:?\s*(closing soon|opening soon|upcoming|closed|open)\b",
        text[:4000], flags=re.I,
    )
    status_text = status_match.group(1) if status_match else text[:1000]
    opportunity.status = _normalise_status(status_text, opportunity.deadline)
    if opportunity.source == "Wellcome" and re.search(
        r"apply anytime|applications (?:can be submitted at any time|are accepted year.round)",
        text[:2500], re.I,
    ):
        opportunity.status = "Open"
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


def classify_funding_support(opportunity: Opportunity, extra_text: str = "") -> Opportunity:
    """Identify dedicated awards versus eligible expenses in bigger grants.

    Treat funding exclusions as negative evidence, not advertised support.
    Travel/support terms are matched in local text windows rather than counting
    every use of the word 'travel' across an entire funding policy.
    """
    title = _clean(opportunity.title + " " + opportunity.programme)
    description = _clean(" ".join([opportunity.summary, opportunity.eligibility, extra_text]))
    dedicated = bool(any(re.search(p, title, re.I) for p in DEDICATED_PATTERNS))
    categories = []
    evidence = ""
    for category, patterns in SUPPORT_PATTERNS.items():
        title_hit = next((re.search(pattern, title, re.I) for pattern in patterns
                          if re.search(pattern, title, re.I)), None)
        description_hit = next((re.search(pattern, description, re.I) for pattern in patterns
                                if re.search(pattern, description, re.I)), None)
        hit, text = (title_hit, title) if title_hit else (description_hit, description)
        if not hit:
            continue
        window = text[max(0, hit.start() - 100): min(len(text), hit.end() + 110)]
        # Exclusion of standalone travel is different from travel being eligible
        # under a broader award; only reject when a negative statement is all
        # that is evidenced in this local window.
        if not title_hit and any(re.search(p, window, re.I) for p in NEGATIVE_SUPPORT_PATTERNS):
            continue
        categories.append(category)
        if not evidence:
            evidence = _clean(window)

    opportunity.funding_types = tuple(categories)
    opportunity.support_basis = (
        "Dedicated bursary / travel award" if categories and dedicated
        else "Eligible costs within a broader award" if categories
        else "Not specified"
    )
    opportunity.support_evidence = evidence
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
    classify_funding_support(opportunity, extra_text=extra_text)
    return opportunity


def _fetch_source(source: str, max_pages: int, enrich_details: bool) -> tuple[list[Opportunity], list[str]]:
    """Fetch a funder's index, specialist listings and verified programme pages.

    Each page is actually retrieved. If access fails we show a source warning,
    not a fictional opportunity. Direct programme pages are always inspected
    because their funding type may be absent from the source's main index.
    """
    if source not in SOURCE_URLS:
        return [], [f"Unsupported funding source: {source}"]

    base_url = SOURCE_URLS[source]
    session = _session()
    opportunities: list[Opportunity] = []
    errors: list[str] = []
    known_urls: set[str] = set()

    # SSA uses a single directory. NIHR also has training and regional awards
    # pages that are outside the main NIHR research funding listings.
    page_count = 1 if source == "SSA" else max_pages
    pages = [(base_url, n) for n in range(1, page_count + 1)]
    if source == "NIHR":
        pages.extend((url, 1) for url in NIHR_EXTRA_PAGES)

    no_more_main_pages = False
    for page_url, page_number in pages:
        if page_url == base_url and no_more_main_pages:
            continue
        try:
            if source == "SSA" or page_url != base_url:
                params = None
            elif source in {"NIHR", "UKRI", "Wellcome"}:
                params = {"page": page_number} if page_number > 1 else None
            else:
                params = {"page": page_number, "order": "-last_published_at"} if page_number > 1 else None

            html = _get_html(session, page_url, params=params)
            if source == "NIHR" and page_url in NIHR_EXTRA_PAGES:
                page_items = parse_nihr_specialist_listing(html, page_url)
            else:
                page_items = parse_listing(html, page_url, source)
            new_items = [item for item in page_items if _canonical_url(item.url) not in known_urls]
            if not new_items and page_url == base_url:
                no_more_main_pages = True
                continue
            for item in new_items:
                known_urls.add(_canonical_url(item.url))
                opportunities.append(item)
        except Exception as exc:
            errors.append(f"{source} ({page_url}), page {page_number}: {type(exc).__name__}: {exc}")
            if page_url == base_url:
                no_more_main_pages = True

    # Read a capped number of directory results to avoid excessive requests.
    # Even with detail-reading switched off, source listings are still tagged
    # using their available summaries/titles.
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

    # Some societies have dedicated travel schemes which never appear near the
    # top of their general research-call feed. Fetch those official pages too.
    # They are NEVER considered verified/open until the page is accessible.
    for programme in FEATURED_PROGRAMMES.get(source, ()):
        url = programme["url"]
        if _canonical_url(url) in known_urls:
            continue
        try:
            html = _get_html(session, url)
            item = Opportunity(
                source=source,
                funder=FUNDER_NAMES[source],
                title=programme["title"],
                url=url,
                summary=programme["summary"],
                eligibility=programme.get("eligibility", ""),
                programme=programme.get("programme", ""),
                amount=programme.get("amount", ""),
                status="Unknown",
            )
            # Parse the LIVE programme page to refresh status, amount and
            # eligibility where available and categorise its funding support.
            item = enrich_opportunity(item, html)
            opportunities.append(item)
            known_urls.add(_canonical_url(url))
            time.sleep(0.08)
        except Exception as exc:
            errors.append(f"{source} programme ({url}): {type(exc).__name__}: {exc}")

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
    deduped = {_canonical_url(item.url): item for item in all_items}
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
    funding_types: list[str] | None = None,
    include_general_support: bool = True,
    support_arrangement: str = "Any arrangement",
) -> list[Opportunity]:
    terms = _custom_terms(custom_query)
    wanted_types = set(funding_types or ())
    results = []
    for opportunity in opportunities:
        searchable = " ".join(
            [opportunity.title, opportunity.summary, opportunity.programme, opportunity.eligibility]
        ).lower()
        support_match = bool(set(opportunity.funding_types) & wanted_types) if wanted_types else bool(opportunity.funding_types)
        if wanted_types and not support_match:
            continue
        # Restrict to dedicated grants or eligible expenses when requested.
        if support_arrangement == "Dedicated awards only" and not opportunity.support_basis.startswith("Dedicated"):
            continue
        if support_arrangement == "Costs within larger grants only" and not opportunity.support_basis.startswith("Eligible"):
            continue
        selected_topic_match = bool(set(topics) & set(opportunity.matched_topics))
        custom_match = bool(terms) and any(term in searchable for term in terms)
        relevance_match = (selected_topic_match or custom_match or
                           (include_broad and opportunity.broad_call) or
                           (include_general_support and support_match))
        if not topics and not terms and not wanted_types:
            relevance_match = (include_broad and opportunity.broad_call) or (include_general_support and support_match)
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
                "Funding support": ", ".join(item.funding_types),
                "Support arrangement": item.support_basis,
                "Support evidence": item.support_evidence,
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
