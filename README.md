# Funding Opportunity Finder

A Streamlit application that collects funding calls from:

- The British Academy: <https://www.thebritishacademy.ac.uk/funding/>
- NIHR: <https://www.nihr.ac.uk/funding-opportunities>
- Wellcome: <https://wellcome.org/research-funding/schemes>
- UKRI: <https://www.ukri.org/opportunity/>

It classifies opportunities related to alcohol, addiction, mental health and connected health topics. It can also include broad researcher-led or interdisciplinary calls whose remit may accept a relevant proposal even when the topic is not written in the call title.

## Important security action

The original script supplied for this project contained a Google Gemini API key in plain text. Revoke or rotate that key immediately. This replacement does not use Gemini and never includes an API key in the source code.

## Run locally

Python 3.10 or newer is recommended.

```bash
python -m venv .venv
```

Activate the environment:

```bash
# macOS/Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Install and launch:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Optional Groq GPT-OSS setup

The app works without AI by using deterministic topic matching. To add Groq-powered relevance review and concise summaries, create a Groq API key and expose it as `GROQ_API_KEY`:

```bash
# macOS/Linux
export GROQ_API_KEY="your-key"

# Windows PowerShell
$env:GROQ_API_KEY="your-key"
```

For Streamlit Community Cloud, add `GROQ_API_KEY` in the app's Secrets settings. For local Streamlit secrets, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and replace the placeholder. That file is ignored by Git and must never be committed.

The default model is `openai/gpt-oss-20b`; `openai/gpt-oss-120b` is also selectable. GPT-OSS receives only the public title, summary, programme and eligibility text collected from funding pages. It adds separate AI fields and does not overwrite source dates, award values or eligibility.

## How date filtering works

- **Opened in the last 7 days / 3 months** uses the opportunity's opening date.
- **Deadline in the next 7 days / 3 months** uses its closing date.
- The two custom choices let you select an exact opening-date or deadline range.
- If a funder does not publish the selected date, that opportunity is excluded while the date filter is active.

## Search behaviour

The app always uses deterministic keyword families first, so it remains usable without an LLM. You can add custom comma-separated terms in the sidebar. Enable **Include broad/remit-based calls** to find general calls that could accept addiction or mental-health research. Groq GPT-OSS is an optional second-pass review; the app clearly separates its assessment from funder-provided information.

## Reliability notes

Funding websites change their HTML from time to time. The parsers use multiple structural fallbacks, caching and retries, but selectors may eventually require maintenance. The app shows source warnings rather than silently returning invented data. Always open the original call and confirm its deadline, eligibility and scope before applying.

The app caches fetched pages for 30 minutes to limit requests. Do not reduce this aggressively; the funders' websites should be queried respectfully.

## Run parser tests

```bash
python -m unittest discover -s tests -v
```
