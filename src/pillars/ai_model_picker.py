"""Use-case-aware Azure AI model recommendations.

For a given AI workload (chatbot / coding / reasoning / RAG / vision /
translation / embeddings / image gen / speech) the picker recommends:

  1. The PRIMARY Azure OpenAI 1st-party model that fits the use case
     (always preferred per the customer's deployment + compliance
     boundary — Azure OpenAI runs inside the customer's tenant, has
     SLA / data-residency commitments, and is the path Microsoft sales
     pushes for enterprise deals).

  2. An Azure AI Foundry 3rd-party fallback (Meta Llama / Mistral /
     DeepSeek / Cohere / Microsoft Phi) ONLY when the use case has a
     genuine reason to prefer it:

       - Open-weight licensing required (regulator demands customer
         control of model weights / on-prem export)
       - Cost sensitivity at scale where the 3rd party is materially
         cheaper at equivalent quality (e.g. Llama 3.3 70B for chat
         is roughly equal quality to GPT-4o at ~30% of the input cost)
       - Specific capability the 1st party doesn't currently match
         (e.g. DeepSeek-R1's chain-of-thought traces; Codestral's
         FIM token for inline code completion)

Microsoft pricing benchmarks captured October 2025 (USD per million
tokens, input / output). Refresh when new models ship.

References:
  https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models
  https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/foundry-models-overview
  https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/models-billing
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Tuple


@dataclass(frozen=True)
class ModelRec:
    key: str                   # matches AZURE_OPENAI_MODELS / AZURE_FOUNDRY_MODELS key
    provider: str              # "azure_openai" or "azure_foundry"
    label: str                 # human-readable label
    in_per_m: float            # USD per million input tokens (PAYG, Oct-2025)
    out_per_m: float           # USD per million output tokens
    rationale: str             # why this model for this use case


# Use-case taxonomy. Keys are stable internal identifiers used by the
# detection logic + Sonnet auto-simulate prompt. Each maps to a primary
# Azure OpenAI pick + optional Foundry fallback for the documented
# reason above.
USE_CASES: Tuple[str, ...] = (
    "chatbot_internal",       # internal staff Q&A, HR, policy lookup
    "chatbot_customer",       # public-facing customer support
    "rag_docs",               # document corpus search + answer w/ citations
    "code_assistant",         # code gen / refactor / review
    "reasoning_heavy",        # multi-step reasoning, math, analysis
    "summarization",          # doc / meeting / transcript summarisation
    "translation",            # multilingual translation
    "vision_ocr",             # image understanding, OCR, document scan
    "image_generation",       # text-to-image creative
    "speech_stt",             # speech to text transcription
    "embeddings",             # vector embeddings for semantic search / RAG
    "general",                # default / mixed / unclear
)


# Primary + fallback model picks per use case + mode bucket. Picks
# encode current Microsoft Learn guidance + community benchmarks
# (lmarena.ai / artificialanalysis.ai snapshots, Oct-2025).
_PRIMARY_BY_USE_CASE = {
    "chatbot_internal": {
        "saving":    ModelRec("gpt-4o-mini",  "azure_openai", "GPT-4o mini",
                              0.15, 0.60,
                              "Internal staff chat doesn't need GPT-4o's reasoning; "
                              "GPT-4o mini handles policy lookup + RAG retrieval "
                              "at 1/15th the input cost."),
        "normal":    ModelRec("gpt-4o-mini",  "azure_openai", "GPT-4o mini",
                              0.15, 0.60,
                              "Same as saving — for internal chat there's no "
                              "quality cliff between 4o-mini and 4o."),
        "high_perf": ModelRec("gpt-4o",       "azure_openai", "GPT-4o",
                              2.50, 10.00,
                              "When latency matters or the queries require "
                              "nuanced policy interpretation, GPT-4o's deeper "
                              "reasoning earns its premium for internal staff."),
    },
    "chatbot_customer": {
        "saving":    ModelRec("gpt-4o-mini",  "azure_openai", "GPT-4o mini",
                              0.15, 0.60,
                              "Cost-sensitive consumer chat at scale; 4o-mini "
                              "handles typical FAQ + Tier-1 support."),
        "normal":    ModelRec("gpt-4o",       "azure_openai", "GPT-4o",
                              2.50, 10.00,
                              "Brand-facing chat needs higher coherence + "
                              "tone control — GPT-4o is the safe default."),
        "high_perf": ModelRec("gpt-4.1",      "azure_openai", "GPT-4.1",
                              2.00, 8.00,
                              "GPT-4.1 (2025) has 1M-token context for long "
                              "session memory + better instruction-following "
                              "than 4o, at a lower price point."),
    },
    "rag_docs": {
        "saving":    ModelRec("gpt-4o-mini",  "azure_openai", "GPT-4o mini",
                              0.15, 0.60,
                              "RAG handlers consume mostly retrieved context; "
                              "mini is sufficient because the model just "
                              "needs to synthesise the retrieved chunks."),
        "normal":    ModelRec("gpt-4o",       "azure_openai", "GPT-4o",
                              2.50, 10.00,
                              "Multi-hop RAG with nuanced citation requirements "
                              "benefits from GPT-4o's reasoning."),
        "high_perf": ModelRec("gpt-4.1",      "azure_openai", "GPT-4.1",
                              2.00, 8.00,
                              "GPT-4.1's 1M-context window lets you skip the "
                              "chunking-and-retrieving step for medium corpora "
                              "(<800K tokens)."),
    },
    "code_assistant": {
        "saving":    ModelRec("gpt-4.1-mini", "azure_openai", "GPT-4.1 mini",
                              0.40, 1.60,
                              "GPT-4.1 mini is coding-tuned (2025) and beats "
                              "GPT-4o-mini on HumanEval at a similar price."),
        "normal":    ModelRec("gpt-4.1",      "azure_openai", "GPT-4.1",
                              2.00, 8.00,
                              "GPT-4.1 is Microsoft's flagship coding model; "
                              "tops most coding benchmarks late-2025."),
        "high_perf": ModelRec("o3-mini",      "azure_openai", "o3 mini (reasoning)",
                              1.10, 4.40,
                              "o3 mini's chain-of-thought reasoning helps for "
                              "complex refactoring / debugging — cheaper than "
                              "full o3 / o1 with most of the quality."),
    },
    "reasoning_heavy": {
        "saving":    ModelRec("o3-mini",      "azure_openai", "o3 mini (reasoning)",
                              1.10, 4.40,
                              "Cheapest reasoning model in the o-series; "
                              "fits multi-step math + analysis without paying "
                              "o3/o1 premium."),
        "normal":    ModelRec("o3-mini",      "azure_openai", "o3 mini (reasoning)",
                              1.10, 4.40,
                              "Same as saving — o3 mini hits the sweet spot for "
                              "production reasoning workloads."),
        "high_perf": ModelRec("o3",           "azure_openai", "o3 (reasoning)",
                              15.00, 60.00,
                              "Full o3 reasoning when accuracy on hardest "
                              "problems matters more than cost."),
    },
    "summarization": {
        "saving":    ModelRec("gpt-4o-mini",  "azure_openai", "GPT-4o mini",
                              0.15, 0.60,
                              "Summarisation is mostly compression; 4o-mini "
                              "is more than enough for transcript / meeting / "
                              "email summarisation."),
        "normal":    ModelRec("gpt-4o-mini",  "azure_openai", "GPT-4o mini",
                              0.15, 0.60,
                              "No upgrade needed — summarisation isn't "
                              "reasoning-heavy."),
        "high_perf": ModelRec("gpt-4o",       "azure_openai", "GPT-4o",
                              2.50, 10.00,
                              "Use 4o only when summaries need nuanced tone "
                              "preservation (legal briefs, exec summaries)."),
    },
    "translation": {
        "saving":    ModelRec("gpt-4o-mini",  "azure_openai", "GPT-4o mini",
                              0.15, 0.60,
                              "Best cost/quality for routine translation "
                              "across 50+ languages."),
        "normal":    ModelRec("gpt-4o",       "azure_openai", "GPT-4o",
                              2.50, 10.00,
                              "GPT-4o has the broadest + highest-quality "
                              "multilingual coverage; idiomatic translation "
                              "across rare languages."),
        "high_perf": ModelRec("gpt-4o",       "azure_openai", "GPT-4o",
                              2.50, 10.00,
                              "Same as normal — translation tops out at 4o; "
                              "no need for o-series reasoning."),
    },
    "vision_ocr": {
        "saving":    ModelRec("gpt-4o-mini",  "azure_openai", "GPT-4o mini (vision)",
                              0.15, 0.60,
                              "GPT-4o mini natively handles images + text; "
                              "cheapest path for receipt/invoice OCR."),
        "normal":    ModelRec("gpt-4o",       "azure_openai", "GPT-4o (vision)",
                              2.50, 10.00,
                              "Full GPT-4o vision for chart understanding, "
                              "diagram reading, document layout extraction."),
        "high_perf": ModelRec("gpt-4.1",      "azure_openai", "GPT-4.1 (vision)",
                              2.00, 8.00,
                              "GPT-4.1 has improved vision benchmarks vs 4o "
                              "and supports 1M context for multi-page docs."),
    },
    "image_generation": {
        # DALL-E 3 / GPT-Image-1 are priced per image, not per token —
        # placeholders here so the prompt knows to route image gen
        # through cognitive_services / Foundry instead of the chat
        # token budget.
        "saving":    ModelRec("dall-e-3",     "azure_openai", "DALL-E 3 (standard 1024)",
                              0.0, 0.0,
                              "Standard 1024×1024 is ~$0.04/image; cheapest "
                              "Azure-native creative."),
        "normal":    ModelRec("dall-e-3",     "azure_openai", "DALL-E 3 (HD 1024)",
                              0.0, 0.0,
                              "DALL-E 3 HD at ~$0.08/image when quality "
                              "matters for production assets."),
        "high_perf": ModelRec("gpt-image-1",  "azure_openai", "GPT-Image-1",
                              0.0, 0.0,
                              "GPT-Image-1 (2025) — better prompt adherence "
                              "+ in-image text rendering than DALL-E 3."),
    },
    "speech_stt": {
        # Whisper STT priced per audio hour, not per token. Route via
        # cognitive_services bucket.
        "saving":    ModelRec("whisper",      "azure_openai", "Whisper STT",
                              0.0, 0.0,
                              "$0.36/audio-hour transcription — cheapest "
                              "Azure-native STT."),
        "normal":    ModelRec("whisper",      "azure_openai", "Whisper STT",
                              0.0, 0.0,
                              "Same as saving — Whisper is the standard."),
        "high_perf": ModelRec("gpt-4o-audio", "azure_openai", "GPT-4o Audio",
                              0.0, 0.0,
                              "GPT-4o Audio for low-latency conversational "
                              "voice apps (responds in audio directly)."),
    },
    "embeddings": {
        "saving":    ModelRec("text-embedding-3-small", "azure_openai", "Embedding v3 (small)",
                              0.02, 0.02,
                              "$0.02/M tokens — cheapest embedding for RAG; "
                              "1536 dims, good retrieval quality at small scale."),
        "normal":    ModelRec("text-embedding-3-small", "azure_openai", "Embedding v3 (small)",
                              0.02, 0.02,
                              "Default for most RAG workloads; upgrade only "
                              "if retrieval quality benchmarks demand it."),
        "high_perf": ModelRec("text-embedding-3-large", "azure_openai", "Embedding v3 (large)",
                              0.13, 0.13,
                              "$0.13/M, 3072 dims; ~5% better retrieval "
                              "precision on long-form technical corpora."),
    },
    "general": {
        "saving":    ModelRec("gpt-4o-mini",  "azure_openai", "GPT-4o mini",
                              0.15, 0.60,
                              "Safest cost-conscious default — handles 80% "
                              "of common LLM tasks at $0.15/M input."),
        "normal":    ModelRec("gpt-4o-mini",  "azure_openai", "GPT-4o mini",
                              0.15, 0.60,
                              "Default for unspecified use case; user upgrades "
                              "via the model dropdown if needed."),
        "high_perf": ModelRec("gpt-4o",       "azure_openai", "GPT-4o",
                              2.50, 10.00,
                              "Production-grade default when use case is "
                              "unclear but quality matters."),
    },
}


# 3rd-party Foundry fallback candidates — only suggested when the
# Sonnet prompt or text-hints explicitly call for one of these
# scenarios (open-weight / on-prem export, materially cheaper at
# scale, specific capability the 1st-party lacks).
_FOUNDRY_FALLBACKS = {
    "chatbot_customer":  ("llama-3.3-70b",
                          "Llama 3.3 70B (open-weight, ~$0.71/M input) — "
                          "~30% cheaper than GPT-4o for high-volume customer chat "
                          "where the customer wants weight ownership for "
                          "compliance OR plans to fine-tune on their corpus."),
    "code_assistant":    ("codestral",
                          "Mistral Codestral — supports FIM (fill-in-the-middle) "
                          "tokens that GPT-4.1 doesn't, useful for inline IDE "
                          "completion. Otherwise stay on GPT-4.1."),
    "reasoning_heavy":   ("deepseek-r1",
                          "DeepSeek-R1 — open-weight reasoning model with full "
                          "chain-of-thought traces (useful when the customer "
                          "needs to audit the reasoning path)."),
    "rag_docs":          ("cohere-command-r-plus",
                          "Cohere Command R+ — purpose-built for RAG with "
                          "stronger citation accuracy than GPT-4o; consider "
                          "when document attribution is a hard requirement."),
    "vision_ocr":        ("phi-4-multimodal",
                          "Phi-4 Multimodal — lightweight (14B) and can be "
                          "self-hosted on a smaller GPU if data residency "
                          "blocks cloud APIs."),
}


def _resolve_mode(mode: str) -> str:
    return mode if mode in ("saving", "normal", "high_perf") else "normal"


def recommend_ai_model(
    use_case: str,
    mode: str = "normal",
    prefer_third_party: bool = False,
) -> Tuple[ModelRec, ModelRec | None]:
    """Return (primary_pick, optional_third_party_alternative).

    First-party Azure OpenAI is ALWAYS the primary pick. The Foundry
    fallback is only populated when the use case has a documented
    reason to consider an alternative (open-weight requirement,
    cost-at-scale, specific capability gap) — Sonnet's prompt also
    references this rule, but `prefer_third_party=True` forces the
    fallback to be returned even when no scenario triggers it
    (e.g. user explicitly asked for the cheaper option)."""
    use_case = use_case if use_case in USE_CASES else "general"
    mode = _resolve_mode(mode)
    primary = _PRIMARY_BY_USE_CASE[use_case][mode]

    fallback_key, fallback_rationale = _FOUNDRY_FALLBACKS.get(use_case, (None, None))
    if fallback_key is None and not prefer_third_party:
        return primary, None

    # Build a synthetic ModelRec for the Foundry fallback if any
    if fallback_key is None:
        return primary, None

    foundry_rec = ModelRec(
        key=fallback_key,
        provider="azure_foundry",
        label=f"Foundry — {fallback_key}",
        in_per_m=0.0, out_per_m=0.0,    # priced via Foundry meter, not embedded here
        rationale=fallback_rationale,
    )
    return primary, foundry_rec


# Heuristic — map upload text hints to a use case. Returns "general"
# when no clear signal. Multiple matches → first hit wins (matches the
# order in the regex list, which goes most-specific → least-specific).
_USE_CASE_PATTERNS: List[Tuple[str, Tuple[str, ...]]] = [
    ("speech_stt",       ("speech to text", "transcrib", "whisper", "stt ", "speech-to-text",
                          "voice transcription", "call recording transcript")),
    ("image_generation", ("image generation", "text-to-image", "text to image", "dall-e",
                          "dalle", "image synthesis", "logo generation", "creative image",
                          "marketing visual")),
    ("vision_ocr",       ("ocr ", "document scan", "invoice extraction", "receipt extract",
                          "vision model", "image understanding", "chart reading",
                          "diagram extract", "pdf image")),
    ("code_assistant",   ("code generation", "code review", "refactor", "coding assistant",
                          "copilot", "ide integration", "code complet", "code suggest",
                          "developer productivity tool")),
    ("reasoning_heavy",  ("multi-step reasoning", "complex math", "logic puzzle",
                          "research assistant", "deep analysis", "agentic workflow",
                          "tool calling", "function calling")),
    ("translation",      ("translation", "translate ", "multilingual", "localization",
                          "language pair", "i18n")),
    ("summarization",    ("summariz", "summary of", "meeting summar", "transcript summar",
                          "executive summary", "report summar")),
    ("rag_docs",         ("rag ", "retrieval augmented", "retrieval-augmented",
                          "document q&a", "doc q&a", "knowledge base", "policy lookup",
                          "knowledge retrieval", "vector search", "semantic search",
                          "ai search", "azure search")),
    ("embeddings",       ("embedding", "vector store", "vector db", "semantic search",
                          "similarity search")),
    ("chatbot_internal", ("internal chatbot", "staff chatbot", "hr chatbot", "hr bot",
                          "policy chatbot", "internal q&a", "intranet bot",
                          "employee assistant")),
    ("chatbot_customer", ("customer chatbot", "customer-facing", "public chatbot",
                          "support chatbot", "support bot", "consumer chatbot",
                          "b2c chatbot", "external chatbot", "live chat")),
]


def detect_ai_use_case(*hints: Any) -> str:
    """Inspect text hints and return the best-fit use case identifier.
    Falls back to 'general' when no specific pattern matches."""
    blob = " ".join(_flatten_text(h) for h in hints).lower()
    for uc, kws in _USE_CASE_PATTERNS:
        if any(k in blob for k in kws):
            return uc
    return "general"


def _flatten_text(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, (list, tuple, set)):
        return " ".join(_flatten_text(i) for i in x)
    if isinstance(x, dict):
        return " ".join(_flatten_text(v) for v in x.values())
    return str(x)
