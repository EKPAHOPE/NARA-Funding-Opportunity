from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from funding_scraper import Opportunity


GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODELS = ("openai/gpt-oss-20b", "openai/gpt-oss-120b")


def _parse_ai_results(content: str) -> list[dict[str, Any]]:
    """Parse and validate the small, non-authoritative AI assessment payload."""
    payload = json.loads(content)
    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        raise ValueError("Groq response did not contain a results list")

    results: list[dict[str, Any]] = []
    for raw in raw_results:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), int):
            continue
        relevant = raw.get("relevant")
        if not isinstance(relevant, bool):
            continue
        results.append(
            {
                "id": raw["id"],
                "relevant": relevant,
                "summary": str(raw.get("summary", "")).strip()[:400],
                "reason": str(raw.get("reason", "")).strip()[:240],
            }
        )
    return results


def enrich_with_groq(
    opportunities: list[Opportunity],
    api_key: str,
    model: str = GROQ_MODELS[0],
    topics: list[str] | None = None,
    custom_query: str = "",
    max_items: int = 24,
) -> tuple[list[Opportunity], list[str]]:
    """Add relevance assessments and concise summaries using Groq GPT-OSS.

    Dates, amounts, eligibility and source text are never changed by the model.
    """
    if not api_key.strip():
        return opportunities, ["Groq AI was enabled, but no GROQ_API_KEY was provided."]
    if model not in GROQ_MODELS:
        return opportunities, [f"Unsupported Groq model: {model}"]

    client = OpenAI(api_key=api_key.strip(), base_url=GROQ_BASE_URL)
    errors: list[str] = []
    ranked = sorted(opportunities, key=lambda item: item.relevance_score, reverse=True)[:max_items]
    selected_topics = ", ".join(topics or []) or "alcohol, addiction, mental health, and related health"

    for start in range(0, len(ranked), 8):
        batch = ranked[start : start + 8]
        public_records = [
            {
                "id": index,
                "title": item.title,
                "source": item.source,
                "summary": item.summary[:700],
                "programme": item.programme[:250],
                "eligibility": item.eligibility[:500],
                "keyword_topics": list(item.matched_topics),
            }
            for index, item in enumerate(batch)
        ]
        prompt = {
            "research_topics": selected_topics,
            "additional_terms": custom_query,
            "opportunities": public_records,
        }
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You classify public research-funding listings. Use only the supplied text. "
                            "Return valid JSON with one result per input: "
                            '{"results":[{"id":0,"relevant":true,"summary":"max 45 words",'
                            '"reason":"max 30 words"}]}. '
                            "Relevant means the call plausibly supports the requested research topics, "
                            "including broad calls whose remit could accept them. Do not invent or infer "
                            "deadlines, money, eligibility, organisations, or application requirements."
                        ),
                    },
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_completion_tokens=1800,
            )
            content = response.choices[0].message.content or "{}"
            for result in _parse_ai_results(content):
                if 0 <= result["id"] < len(batch):
                    item = batch[result["id"]]
                    item.ai_relevant = result["relevant"]
                    item.ai_summary = result["summary"]
                    item.ai_reason = result["reason"]
        except Exception as exc:
            errors.append(f"Groq batch {start // 8 + 1}: {type(exc).__name__}: {exc}")

    return opportunities, errors
