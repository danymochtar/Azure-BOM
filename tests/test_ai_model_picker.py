"""AI use-case → 1st-party / Foundry model recommendation tests.

Covers the 12 use-case taxonomy + 3 mode tiers + Foundry-alt rules
so the picker logic stays in sync with the Microsoft Learn guidance
embedded in `src/pillars/ai_model_picker.py`.
"""
from __future__ import annotations

import pytest

from src.pillars.ai_model_picker import (
    detect_ai_use_case,
    recommend_ai_model,
    USE_CASES,
)


# ---- detect_ai_use_case ----------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Internal staff HR chatbot for company policies",      "chatbot_internal"),
    ("Customer-facing support chatbot for our e-commerce",  "chatbot_customer"),
    ("RAG over insurance policy documents with citations",  "rag_docs"),
    ("Coding assistant for Visual Studio Code",             "code_assistant"),
    ("Multi-step reasoning for legal contract analysis",    "reasoning_heavy"),
    ("Summarize meeting transcripts every Friday",          "summarization"),
    ("Translation pipeline for 20 languages",               "translation"),
    ("OCR receipts and invoices for AP automation",         "vision_ocr"),
    ("Generate marketing visuals via DALL-E",               "image_generation"),
    ("Whisper transcription of customer calls",             "speech_stt"),
    ("Vector store + similarity search backend",            "embeddings"),
    ("Insurance exclusion AI solution",                     "general"),
])
def test_use_case_detection(text, expected):
    assert detect_ai_use_case(text) == expected


# ---- recommend_ai_model — 1st-party priority -------------------------

def test_internal_chatbot_picks_4o_mini():
    primary, _ = recommend_ai_model("chatbot_internal", "saving")
    assert primary.key == "gpt-4o-mini"
    assert primary.provider == "azure_openai"


def test_customer_chatbot_hp_picks_gpt_41():
    primary, _ = recommend_ai_model("chatbot_customer", "high_perf")
    assert primary.key == "gpt-4.1"


def test_code_assistant_normal_picks_gpt_41():
    primary, _ = recommend_ai_model("code_assistant", "normal")
    assert primary.key == "gpt-4.1"


def test_reasoning_heavy_picks_o3_series():
    primary, _ = recommend_ai_model("reasoning_heavy", "normal")
    assert primary.key.startswith("o3")


def test_embeddings_picks_v3_small_in_saving():
    primary, _ = recommend_ai_model("embeddings", "saving")
    assert primary.key == "text-embedding-3-small"


def test_embeddings_upgrades_to_large_in_hp():
    primary, _ = recommend_ai_model("embeddings", "high_perf")
    assert primary.key == "text-embedding-3-large"


# ---- Foundry 3rd-party alternatives ---------------------------------

def test_chatbot_customer_offers_llama_fallback():
    """Customer-facing chat at scale → Foundry Llama 3.3 70B suggested
    for cost-at-scale + open-weight."""
    primary, fallback = recommend_ai_model("chatbot_customer", "normal")
    assert primary.provider == "azure_openai"
    assert fallback is not None
    assert "llama" in fallback.key


def test_code_assistant_offers_codestral_fallback():
    """Code assistant → Foundry Codestral suggested for FIM tokens."""
    _, fallback = recommend_ai_model("code_assistant", "normal")
    assert fallback is not None
    assert "codestral" in fallback.key


def test_reasoning_offers_deepseek_fallback():
    _, fallback = recommend_ai_model("reasoning_heavy", "normal")
    assert fallback is not None
    assert "deepseek" in fallback.key


def test_summarization_no_fallback():
    """Summarisation doesn't have a documented reason to prefer
    Foundry — 1st-party only."""
    primary, fallback = recommend_ai_model("summarization", "normal")
    assert primary.provider == "azure_openai"
    assert fallback is None


# ---- Mode tiering ----------------------------------------------------

def test_pricing_increases_with_mode():
    """For non-mini models, HP costs more than saving."""
    saving, _ = recommend_ai_model("rag_docs", "saving")
    hp, _ = recommend_ai_model("rag_docs", "high_perf")
    assert hp.in_per_m >= saving.in_per_m


def test_unknown_use_case_falls_to_general():
    """Invalid use_case parameter shouldn't crash — fall through to
    'general' (default catch-all)."""
    primary, _ = recommend_ai_model("nonexistent_use_case", "normal")
    assert primary is not None
    assert primary.provider == "azure_openai"


def test_all_use_cases_have_picks_per_mode():
    """Smoke: every (use_case, mode) pair returns a valid pick."""
    for uc in USE_CASES:
        for mode in ("saving", "normal", "high_perf"):
            primary, _ = recommend_ai_model(uc, mode)
            assert primary is not None, f"{uc}/{mode} returned None"
            assert primary.provider in ("azure_openai", "azure_foundry")
