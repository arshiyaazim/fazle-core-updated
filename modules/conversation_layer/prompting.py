"""Prompt assembly for the shadow-only recruitment conversation layer."""
from __future__ import annotations

from .playbooks import CANONICAL_FACTS, APPLICATION_FIELDS, RecruitmentSignals


def build_recruitment_prompt_context(
    message: str,
    signals: RecruitmentSignals,
    kb_reply: str | None = None,
    rag_context: str | None = None,
    history: str | None = None,
) -> str:
    """Return context text passed to the existing Ollama client as DB context.

    The live Ollama helper owns the base system prompt. This context keeps the
    richer layer scoped to public recruitment facts and reply rules.
    """
    facts = [
        "Shadow recruitment conversation layer rules:",
        "- Bangla-first, short, human, 1-4 lines.",
        "- Answer all recruitment/general inquiry questions in one reply when possible.",
        "- Ask only one next-step question or request one compact set of applicant details.",
        "- Do not discuss payroll, roster, escort program operations, vessel names, private employee data, or internal finance.",
        "- Do not promise an exact salary; say it depends on duty, experience, and office verification.",
        "- If user is suspicious, address trust first and invite office verification.",
        f"- Application fields to collect: {APPLICATION_FIELDS}.",
        "",
        f"Detected focus: {signals.focus}",
        f"Candidate temperature: {signals.temperature}",
        f"Risk mode: {signals.risk}",
        "",
        "Canonical public facts:",
    ]
    for key in (signals.focus, "salary", "training", "ship_duty", "documents", "office_location", "trust"):
        if key in CANONICAL_FACTS:
            facts.append(f"- {key}: {CANONICAL_FACTS[key]}")

    if kb_reply:
        facts.extend(["", "Current fazle-core KB reply for reference:", kb_reply])
    if rag_context:
        facts.extend(["", "Safe RAG context:", rag_context[:1500]])
    if history:
        facts.extend(["", "Recent conversation history:", history[:1200]])

    facts.extend(["", f"Incoming message: {message[:500]}"])
    return "\n".join(facts)
