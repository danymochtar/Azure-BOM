"""AI Application & Development pillar.

Covers the latest Azure AI catalog:
- **Azure OpenAI**: GPT-4.1 family, GPT-4o family, o-series reasoning, embeddings
- **Azure AI Foundry Models (Models-as-a-Service)**: Llama 3.x, Mistral, DeepSeek,
  Phi — serverless token pricing
- **Azure AI Search**: Basic / S1-S3 / L1-L2 × replicas × partitions
- **GPU VMs**: NC / ND / NCads A100 families via `vm_price` (RI/SP honored)
- **Fine-tuning**: per-1K-token training meter
- **Cognitive Services**: Vision, Language, Translator, Speech, Document
  Intelligence, Content Safety — transaction-based pricing
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..models import BomLine
from ..pricing.retail import HOURS_PER_MONTH, RetailPricesClient
from .azure_security import _pick


PILLAR_METADATA = {
    "key": "ai_application",
    "label": "AI Application & Development",
    "description": "Azure OpenAI, AI Foundry (Llama/Mistral/DeepSeek), AI Search, GPU, Cognitive Services.",
    "icon": "🤖",
    "needs_vm_extraction": False,
    "default_strategy": None,
}


# ---------------------------------------------------------------------------
# Azure OpenAI model catalog (latest)
# ---------------------------------------------------------------------------

@dataclass
class OpenAIModel:
    key: str
    label: str
    input_meter: str
    output_meter: str


AZURE_OPENAI_MODELS: Dict[str, OpenAIModel] = {
    # Reasoning / o-series
    "o3":                    OpenAIModel("o3",                    "o3 (reasoning)",           "o3 inp",               "o3 outp"),
    "o3-mini":               OpenAIModel("o3-mini",               "o3 mini (reasoning)",      "o3 mini inp",          "o3 mini outp"),
    "o1":                    OpenAIModel("o1",                    "o1 (reasoning)",           "o1 inp",               "o1 outp"),
    "o1-mini":               OpenAIModel("o1-mini",               "o1 mini (reasoning)",      "o1 mini inp",          "o1 mini outp"),
    # GPT-4.1 family (latest)
    "gpt-4.1":               OpenAIModel("gpt-4.1",               "GPT-4.1",                  "gpt-4.1 inp",          "gpt-4.1 outp"),
    "gpt-4.1-mini":          OpenAIModel("gpt-4.1-mini",          "GPT-4.1 mini",             "gpt-4.1 mini inp",     "gpt-4.1 mini outp"),
    "gpt-4.1-nano":          OpenAIModel("gpt-4.1-nano",          "GPT-4.1 nano",             "gpt-4.1 nano inp",     "gpt-4.1 nano outp"),
    # GPT-4o family
    "gpt-4o":                OpenAIModel("gpt-4o",                "GPT-4o",                   "gpt-4o inp",           "gpt-4o outp"),
    "gpt-4o-mini":           OpenAIModel("gpt-4o-mini",           "GPT-4o mini",              "gpt-4o mini inp",      "gpt-4o mini outp"),
    # Legacy but still available
    "gpt-4-turbo":           OpenAIModel("gpt-4-turbo",           "GPT-4 Turbo",              "gpt-4 turbo inp",      "gpt-4 turbo outp"),
    "gpt-35-turbo":          OpenAIModel("gpt-35-turbo",          "GPT-3.5 Turbo",            "gpt-3.5 turbo inp",    "gpt-3.5 turbo outp"),
    # Embeddings
    "text-embedding-3-large":OpenAIModel("text-embedding-3-large","Embedding v3 (large)",     "text-embedding-3 large","text-embedding-3 large"),
    "text-embedding-3-small":OpenAIModel("text-embedding-3-small","Embedding v3 (small)",     "text-embedding-3 small","text-embedding-3 small"),
    "text-embedding-ada-002":OpenAIModel("text-embedding-ada-002","Embedding Ada 002",        "text-embedding-ada-002","text-embedding-ada-002"),
}


# ---------------------------------------------------------------------------
# Azure AI Foundry — Models-as-a-Service (serverless)
# ---------------------------------------------------------------------------
# Foundry hosts third-party models (Meta Llama, Mistral, DeepSeek, Microsoft
# Phi, etc.) with per-token retail pricing. Meter names vary by model; we
# search Cognitive Services records with the model name as hint.

@dataclass
class FoundryModel:
    key: str
    label: str
    provider: str
    input_meter: str
    output_meter: str


AZURE_FOUNDRY_MODELS: Dict[str, FoundryModel] = {
    # Meta Llama
    "llama-3.3-70b":  FoundryModel("llama-3.3-70b",  "Llama 3.3 70B Instruct",   "Meta",      "llama-3.3 70b inp",   "llama-3.3 70b outp"),
    "llama-3.1-405b": FoundryModel("llama-3.1-405b", "Llama 3.1 405B Instruct",  "Meta",      "llama-3.1 405b inp",  "llama-3.1 405b outp"),
    "llama-3.1-70b":  FoundryModel("llama-3.1-70b",  "Llama 3.1 70B Instruct",   "Meta",      "llama-3.1 70b inp",   "llama-3.1 70b outp"),
    "llama-3.1-8b":   FoundryModel("llama-3.1-8b",   "Llama 3.1 8B Instruct",    "Meta",      "llama-3.1 8b inp",    "llama-3.1 8b outp"),
    # Mistral
    "mistral-large-2":FoundryModel("mistral-large-2","Mistral Large 2",          "Mistral AI","mistral large 2 inp", "mistral large 2 outp"),
    "mistral-small":  FoundryModel("mistral-small",  "Mistral Small",            "Mistral AI","mistral small inp",   "mistral small outp"),
    "codestral":      FoundryModel("codestral",      "Codestral",                "Mistral AI","codestral inp",       "codestral outp"),
    # DeepSeek
    "deepseek-r1":    FoundryModel("deepseek-r1",    "DeepSeek-R1 (reasoning)",  "DeepSeek",  "deepseek r1 inp",     "deepseek r1 outp"),
    "deepseek-v3":    FoundryModel("deepseek-v3",    "DeepSeek-V3",              "DeepSeek",  "deepseek v3 inp",     "deepseek v3 outp"),
    # Microsoft Phi
    "phi-4":          FoundryModel("phi-4",          "Phi-4 (14B)",              "Microsoft", "phi-4 inp",           "phi-4 outp"),
    "phi-4-multimodal":FoundryModel("phi-4-multimodal","Phi-4 multimodal",      "Microsoft", "phi-4 multimodal inp","phi-4 multimodal outp"),
    "phi-3.5-mini":   FoundryModel("phi-3.5-mini",   "Phi-3.5 mini",             "Microsoft", "phi-3.5 mini inp",    "phi-3.5 mini outp"),
}


# ---------------------------------------------------------------------------
# Azure AI Search
# ---------------------------------------------------------------------------

AI_SEARCH_TIERS: Dict[str, Dict] = {
    "basic":    {"label": "Basic",             "meter_hint": "basic"},
    "standard": {"label": "S1 Standard",       "meter_hint": "s1 standard"},
    "s2":       {"label": "S2",                "meter_hint": "s2"},
    "s3":       {"label": "S3 (High Density)", "meter_hint": "s3"},
    "s3hd":     {"label": "S3 HD (vector)",    "meter_hint": "s3 hd"},
    "l1":       {"label": "L1 (Storage Opt.)", "meter_hint": "l1"},
    "l2":       {"label": "L2 (Storage Opt.)", "meter_hint": "l2"},
}


# ---------------------------------------------------------------------------
# GPU VMs for AI training / inference
# ---------------------------------------------------------------------------

GPU_VM_SKUS: Dict[str, str] = {
    "Standard_NC4as_T4_v3":      "NCas T4 v3   (4 vCPU, 1× T4)",
    "Standard_NC8as_T4_v3":      "NCas T4 v3   (8 vCPU, 1× T4)",
    "Standard_NC16as_T4_v3":     "NCas T4 v3   (16 vCPU, 1× T4)",
    "Standard_NC24ads_A100_v4":  "NCads A100 v4 (24 vCPU, 1× A100 80GB)",
    "Standard_NC48ads_A100_v4":  "NCads A100 v4 (48 vCPU, 2× A100 80GB)",
    "Standard_NC96ads_A100_v4":  "NCads A100 v4 (96 vCPU, 4× A100 80GB)",
    "Standard_ND96asr_v4":       "ND A100 v4   (96 vCPU, 8× A100 40GB)",
    "Standard_ND96amsr_A100_v4": "ND A100 v4   (96 vCPU, 8× A100 80GB)",
    "Standard_ND40rs_v2":        "ND V100      (40 vCPU, 8× V100 32GB)",
    "Standard_NC40ads_H100_v5":  "NC H100 v5   (40 vCPU, 1× H100)",
    "Standard_NC80adis_H100_v5": "NC H100 v5   (80 vCPU, 2× H100)",
    "Standard_ND96isr_H100_v5":  "ND H100 v5   (96 vCPU, 8× H100)",
}


# ---------------------------------------------------------------------------
# Azure AI Cognitive Services catalog
# ---------------------------------------------------------------------------
# Each entry: {label, service_name, meter_hint, unit_prompt, unit_label}.
# `service_name` is the Retail Prices API serviceName filter.

COGNITIVE_SERVICES: Dict[str, Dict] = {
    # `meter_unit_divisor` converts the user's prompt unit (how we ask them
    # to enter) into the retail-meter unit (how Azure bills):
    #   user enters "transactions/month" → meter bills per 1000 transactions
    #     → divisor = 1000 (i.e. user input / 1000 = billable units).
    # This is the source of the 1000× risk flagged by the math audit.
    "vision": {
        "label": "Azure AI Vision",
        "service_name": "Cognitive Services",
        "meter_hint": "computer vision s1",
        "unit_prompt": "Transactions / month",
        "unit_label": "transaction",
        "meter_unit_divisor": 1000.0,   # meter is per 1K transactions
        "notes": "Image analysis, OCR, Face API. ~$1.50 per 1K transactions.",
    },
    "language": {
        "label": "Azure AI Language",
        "service_name": "Cognitive Services",
        "meter_hint": "language s",
        "unit_prompt": "Text records / month (1 record ≈ 1K chars)",
        "unit_label": "1K-char record",
        "meter_unit_divisor": 1000.0,
        "notes": "Sentiment, entity recognition, key phrases, classification.",
    },
    "translator": {
        "label": "Azure AI Translator (Text)",
        "service_name": "Cognitive Services",
        "meter_hint": "translator text s1",
        "unit_prompt": "Characters / month (in millions)",
        "unit_label": "M characters",
        "meter_unit_divisor": 1.0,   # user already enters in millions
        "notes": "Free tier = 2M chars/mo. Standard S1 = $10 per 1M chars.",
    },
    "speech_stt": {
        "label": "Azure AI Speech — Speech-to-text",
        "service_name": "Cognitive Services",
        "meter_hint": "speech to text s",
        "unit_prompt": "Audio hours / month",
        "unit_label": "audio-hour",
        "meter_unit_divisor": 1.0,
        "notes": "Standard real-time + batch transcription.",
    },
    "speech_tts": {
        "label": "Azure AI Speech — Text-to-speech",
        "service_name": "Cognitive Services",
        "meter_hint": "neural text to speech",
        "unit_prompt": "Characters / month (in millions)",
        "unit_label": "M characters",
        "meter_unit_divisor": 1.0,
        "notes": "Neural voices. ~$16 per 1M chars.",
    },
    "document_intelligence": {
        "label": "Azure AI Document Intelligence",
        "service_name": "Azure AI Document Intelligence",
        "meter_hint": "prebuilt",
        "unit_prompt": "Pages analyzed / month",
        "unit_label": "page",
        "meter_unit_divisor": 1000.0,   # meter is per 1K pages
        "notes": "Formerly Form Recognizer. Prebuilt + custom models.",
    },
    "content_safety": {
        "label": "Azure AI Content Safety",
        "service_name": "Cognitive Services",
        "meter_hint": "content safety",
        "unit_prompt": "Transactions / month",
        "unit_label": "transaction",
        "meter_unit_divisor": 1000.0,
        "notes": "Text + image moderation for generative AI applications.",
    },
    "custom_vision": {
        "label": "Azure AI Custom Vision",
        "service_name": "Cognitive Services",
        "meter_hint": "custom vision",
        "unit_prompt": "Transactions / month",
        "unit_label": "transaction",
        "meter_unit_divisor": 1000.0,
        "notes": "Custom image classification models.",
    },
}


# ---------------------------------------------------------------------------
# OpenAI / Foundry line helpers
# ---------------------------------------------------------------------------

def _meter_price(client: RetailPricesClient, region: str, meter_hint: str) -> Optional[float]:
    recs = client.query(
        f"serviceName eq 'Cognitive Services' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    if not recs:
        recs = client.query(
            f"serviceFamily eq 'AI + Machine Learning' and armRegionName eq '{region}' and priceType eq 'Consumption'"
        )
    chosen = _pick(recs, meter_hint)
    return chosen.retail_price if chosen else None


def _token_line(region, model, side, qty_1k, price, app_name, service_display, sku_prefix, currency="USD") -> BomLine:
    priced = price is not None and price > 0
    return BomLine(
        category="AI + ML",
        resource=f"{service_display} {model.label} — {side} tokens",
        sku=model.key,
        meter=f"{model.label} {side}",
        region=region,
        quantity=qty_1k, unit="1K tokens",
        unit_price=price or 0.0,
        monthly_cost=round((price or 0.0) * qty_1k, 2),
        currency=currency,
        source="retail-prices" if priced else "retail-prices-miss",
        service_name=service_display,
        custom_name=f"{app_name}-{sku_prefix}-{model.key}" if app_name else f"{sku_prefix}-{model.key}",
    )


def build_openai_lines(
    client: RetailPricesClient, region: str,
    usage: Dict[str, Dict[str, float]], app_name: str = "",
) -> List[BomLine]:
    lines: List[BomLine] = []
    for model_key, vol in usage.items():
        model = AZURE_OPENAI_MODELS.get(model_key)
        if not model:
            continue
        input_1k_month = round(vol.get("input_1k_per_day", 0) * 30, 2)
        output_1k_month = round(vol.get("output_1k_per_day", 0) * 30, 2)
        if input_1k_month > 0:
            p = _meter_price(client, region, model.input_meter)
            lines.append(_token_line(region, model, "input", input_1k_month, p, app_name,
                                     "Azure OpenAI", "OpenAI"))
        if output_1k_month > 0 and model.output_meter != model.input_meter:
            p = _meter_price(client, region, model.output_meter)
            lines.append(_token_line(region, model, "output", output_1k_month, p, app_name,
                                     "Azure OpenAI", "OpenAI"))
    return lines


def build_foundry_lines(
    client: RetailPricesClient, region: str,
    usage: Dict[str, Dict[str, float]], app_name: str = "",
) -> List[BomLine]:
    """Same shape as build_openai_lines but for Foundry MaaS models."""
    lines: List[BomLine] = []
    for model_key, vol in usage.items():
        model = AZURE_FOUNDRY_MODELS.get(model_key)
        if not model:
            continue
        input_1k_month = round(vol.get("input_1k_per_day", 0) * 30, 2)
        output_1k_month = round(vol.get("output_1k_per_day", 0) * 30, 2)
        if input_1k_month > 0:
            p = _meter_price(client, region, model.input_meter)
            lines.append(_token_line(region, model, "input", input_1k_month, p, app_name,
                                     f"Azure AI Foundry ({model.provider})", "Foundry"))
        if output_1k_month > 0:
            p = _meter_price(client, region, model.output_meter)
            lines.append(_token_line(region, model, "output", output_1k_month, p, app_name,
                                     f"Azure AI Foundry ({model.provider})", "Foundry"))
    return lines


# ---------------------------------------------------------------------------
# AI Search / GPU / fine-tune / cognitive
# ---------------------------------------------------------------------------

def _ai_search_line(
    client: RetailPricesClient, region: str, tier_key: str,
    replicas: int, partitions: int, app_name: str,
) -> Optional[BomLine]:
    if tier_key == "none" or replicas <= 0 or partitions <= 0:
        return None
    cfg = AI_SEARCH_TIERS.get(tier_key)
    if not cfg:
        return None
    recs = client.query(
        f"serviceName eq 'Azure Cognitive Search' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, cfg["meter_hint"])
    if not chosen:
        return None
    units = replicas * partitions
    qty = units * HOURS_PER_MONTH
    return BomLine(
        category="AI + ML",
        resource=f"Azure AI Search {cfg['label']} × {replicas} replicas × {partitions} partitions",
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=round(qty, 2), unit="search-unit-hours",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure AI Search",
        custom_name=f"{app_name}-AISearch-{tier_key}" if app_name else f"AISearch-{tier_key}",
    )


def _gpu_vm_lines(
    client: RetailPricesClient, region: str,
    arm_sku: str, count: int, hours: float, os_is_windows: bool,
    pricing_mode: str, app_name: str, use_ahb: bool = False,
) -> List[BomLine]:
    if count <= 0 or hours <= 0:
        return []
    price = client.vm_price(
        arm_sku, region, os_is_windows=os_is_windows,
        pricing_mode=pricing_mode, use_ahb=use_ahb,
    )
    qty = count * hours
    if not price:
        return [BomLine(
            category="AI + ML",
            resource=f"GPU VM — {arm_sku} x{count}",
            sku=arm_sku, meter="(price not found)",
            region=region, quantity=qty, unit="hours",
            unit_price=0.0, monthly_cost=0.0,
            source="retail-prices-miss",
            service_name="Virtual Machines",
            custom_name=f"{app_name}-GPU-{arm_sku}" if app_name else f"GPU-{arm_sku}",
        )]
    return [BomLine(
        category="AI + ML",
        resource=f"GPU VM — {arm_sku} x{count} ({'Windows' if os_is_windows else 'Linux'})",
        sku=arm_sku, meter=price.meter_name,
        region=region, quantity=qty, unit="hours",
        unit_price=price.retail_price,
        monthly_cost=round(price.retail_price * qty, 2),
        currency=price.currency_code,
        source="retail-prices",
        product_id=price.product_id, sku_id=price.sku_id, meter_id=price.meter_id,
        service_name="Virtual Machines",
        custom_name=f"{app_name}-GPU-{arm_sku}" if app_name else f"GPU-{arm_sku}",
    )]


def _openai_finetune_line(
    client: RetailPricesClient, region: str,
    model_key: str, training_1k_tokens: float, app_name: str,
) -> Optional[BomLine]:
    if training_1k_tokens <= 0:
        return None
    model = AZURE_OPENAI_MODELS.get(model_key)
    if not model:
        return None
    recs = client.query(
        f"serviceName eq 'Cognitive Services' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, f"{model.label.lower()} fine-tun") or _pick(recs, "fine-tun")
    if not chosen:
        return None
    return BomLine(
        category="AI + ML",
        resource=f"Azure OpenAI fine-tuning training — {model.label}",
        sku=model.key, meter=chosen.meter_name,
        region=region, quantity=training_1k_tokens, unit="1K training tokens",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * training_1k_tokens, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure OpenAI Service",
        custom_name=f"{app_name}-OpenAI-FineTune-{model_key}" if app_name else f"OpenAI-FineTune-{model_key}",
    )


def _cognitive_line(
    client: RetailPricesClient, region: str,
    service_key: str, qty: float, app_name: str,
) -> Optional[BomLine]:
    """Cognitive Services line. `qty` is in the user's prompt unit; we divide
    by `meter_unit_divisor` before multiplying by the retail rate so per-1K
    meters (Vision / Doc Intelligence / Content Safety / Custom Vision) bill
    correctly against raw user counts."""
    if qty <= 0:
        return None
    cfg = COGNITIVE_SERVICES.get(service_key)
    if not cfg:
        return None
    recs = client.query(
        f"serviceName eq '{cfg['service_name']}' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, cfg["meter_hint"])
    if not chosen:
        return None
    divisor = float(cfg.get("meter_unit_divisor", 1.0))
    billable_units = qty / divisor if divisor else qty
    return BomLine(
        category="AI + ML",
        resource=(
            f"{cfg['label']} — {qty:,.0f} {cfg['unit_label']}/mo "
            f"(= {billable_units:,.2f} billable units)"
        ),
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=billable_units, unit=chosen.unit_of_measure or "unit",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * billable_units, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name=cfg["label"],
        custom_name=f"{app_name}-CogSvc-{service_key}" if app_name else f"CogSvc-{service_key}",
    )


# Back-compat aliases
def build_ai_ml_bom(client, region, usage, app_name=""):
    return build_openai_lines(client, region, usage, app_name)


# ---------------------------------------------------------------------------
# Pillar contract
# ---------------------------------------------------------------------------

def render_inputs(st, prefs: dict, app_name: str, region: str,
                  upload_bytes: bytes, upload_name: str, profile) -> dict:
    st.caption("Enter token volume / sizing / transactions. Leave 0 to skip a service.")

    # -- Azure OpenAI --
    openai_usage: Dict[str, Dict[str, float]] = {}
    sugg_oai = prefs.get("ai_openai_usage", {}) or {}
    with st.expander("Azure OpenAI — GPT-4.1 / 4o / o-series / embeddings", expanded=True):
        st.caption("Daily token volume per model, in 1K-token units.")
        for mk, model in AZURE_OPENAI_MODELS.items():
            default_in = float((sugg_oai.get(mk) or {}).get("input_1k_per_day", 0.0))
            default_out = float((sugg_oai.get(mk) or {}).get("output_1k_per_day", 0.0))
            c1, c2, c3 = st.columns([2, 1, 1])
            c1.markdown(f"**{model.label}** (`{mk}`)")
            in_1k = c2.number_input(
                "Input 1K/day", min_value=0.0, value=default_in, step=100.0,
                key=f"ai_oai_in_{mk}", label_visibility="collapsed",
            )
            out_1k = c3.number_input(
                "Output 1K/day", min_value=0.0, value=default_out, step=50.0,
                key=f"ai_oai_out_{mk}", label_visibility="collapsed",
            )
            if in_1k > 0 or out_1k > 0:
                openai_usage[mk] = {"input_1k_per_day": in_1k, "output_1k_per_day": out_1k}

    # -- Foundry models (MaaS) --
    foundry_usage: Dict[str, Dict[str, float]] = {}
    sugg_foundry = prefs.get("ai_foundry_usage", {}) or {}
    with st.expander("Azure AI Foundry — Llama / Mistral / DeepSeek / Phi", expanded=False):
        st.caption("Serverless Models-as-a-Service. Same unit convention (1K tokens/day).")
        for mk, model in AZURE_FOUNDRY_MODELS.items():
            default_in = float((sugg_foundry.get(mk) or {}).get("input_1k_per_day", 0.0))
            default_out = float((sugg_foundry.get(mk) or {}).get("output_1k_per_day", 0.0))
            c1, c2, c3 = st.columns([2, 1, 1])
            c1.markdown(f"**{model.label}** · _{model.provider}_ (`{mk}`)")
            in_1k = c2.number_input(
                "Input 1K/day", min_value=0.0, value=default_in, step=100.0,
                key=f"ai_f_in_{mk}", label_visibility="collapsed",
            )
            out_1k = c3.number_input(
                "Output 1K/day", min_value=0.0, value=default_out, step=50.0,
                key=f"ai_f_out_{mk}", label_visibility="collapsed",
            )
            if in_1k > 0 or out_1k > 0:
                foundry_usage[mk] = {"input_1k_per_day": in_1k, "output_1k_per_day": out_1k}

    # -- AI Search --
    sugg_ais = prefs.get("ai_search", {}) or {}
    with st.expander("Azure AI Search (vector / semantic)", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            _tier_keys = ["none"] + list(AI_SEARCH_TIERS.keys())
            _saved_tier = sugg_ais.get("tier", "none")
            _tier_idx = _tier_keys.index(_saved_tier) if _saved_tier in _tier_keys else 0
            tier = st.selectbox(
                "Service tier", _tier_keys, key="aisearch_tier",
                index=_tier_idx,
                format_func=lambda k: "None" if k == "none" else AI_SEARCH_TIERS[k]["label"],
            )
        with c2:
            replicas = st.number_input(
                "Replicas", min_value=0, value=int(sugg_ais.get("replicas", 0)),
                step=1, disabled=(tier == "none"), key="aisearch_replicas",
            )
        with c3:
            partitions = st.number_input(
                "Partitions", min_value=0, value=int(sugg_ais.get("partitions", 0)),
                step=1, disabled=(tier == "none"), key="aisearch_partitions",
            )

    # -- GPU VMs --
    sugg_gpu = prefs.get("gpu_vm", {}) or {}
    with st.expander("GPU VM (training / fine-tuning / self-host inference)", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            _gpu_keys = ["none"] + list(GPU_VM_SKUS)
            _saved_gpu = sugg_gpu.get("sku", "none")
            _gpu_idx = _gpu_keys.index(_saved_gpu) if _saved_gpu in _gpu_keys else 0
            gpu_sku = st.selectbox(
                "GPU SKU", _gpu_keys, index=_gpu_idx, key="gpu_sku",
                format_func=lambda k: "None" if k == "none" else GPU_VM_SKUS[k],
            )
        with c2:
            gpu_count = st.number_input(
                "Instances", min_value=0, value=int(sugg_gpu.get("count", 0)),
                step=1, disabled=(gpu_sku == "none"), key="gpu_count",
            )
        with c3:
            gpu_hours = st.number_input(
                "Hours/month", min_value=0.0, value=float(sugg_gpu.get("hours", 0.0)),
                step=10.0, disabled=(gpu_sku == "none"), key="gpu_hours",
            )
        with c4:
            gpu_os = st.selectbox(
                "OS", ["Linux", "Windows"], disabled=(gpu_sku == "none"), key="gpu_os",
            )

    # -- Fine-tuning --
    sugg_ft = prefs.get("finetune", {}) or {}
    with st.expander("Azure OpenAI fine-tuning training", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _ft_keys = ["none"] + list(AZURE_OPENAI_MODELS)
            _saved_ft = sugg_ft.get("model", "none")
            _ft_idx = _ft_keys.index(_saved_ft) if _saved_ft in _ft_keys else 0
            ft_model = st.selectbox(
                "Base model", _ft_keys, index=_ft_idx, key="ft_model",
                format_func=lambda k: "None" if k == "none" else AZURE_OPENAI_MODELS[k].label,
            )
        with c2:
            ft_tokens = st.number_input(
                "Training tokens (1K)", min_value=0.0,
                value=float(sugg_ft.get("tokens_1k", 0.0)),
                step=100.0, disabled=(ft_model == "none"), key="ft_tokens",
            )

    # -- Cognitive Services --
    sugg_cog = prefs.get("cognitive", {}) or {}
    cognitive_qty: Dict[str, float] = {}
    with st.expander("Azure AI Cognitive Services (Vision / Language / Speech / Doc Intelligence)", expanded=False):
        for sk, cfg in COGNITIVE_SERVICES.items():
            default_q = float(sugg_cog.get(sk, 0.0))
            qty = st.number_input(
                f"{cfg['label']} — {cfg['unit_prompt']}",
                min_value=0.0, value=default_q, step=1.0, key=f"ai_cog_{sk}",
                help=cfg["notes"],
            )
            if qty > 0:
                cognitive_qty[sk] = qty

    return {
        "openai_usage": openai_usage,
        "foundry_usage": foundry_usage,
        "ai_search": {"tier": tier, "replicas": int(replicas), "partitions": int(partitions)},
        "gpu_vm": {
            "sku": gpu_sku, "count": int(gpu_count), "hours": float(gpu_hours),
            "os_windows": (gpu_os == "Windows") if gpu_sku != "none" else False,
        },
        "finetune": {"model": ft_model, "tokens_1k": float(ft_tokens)},
        "cognitive": cognitive_qty,
    }


def build_bom(client, region: str, inputs: dict, app_name: str, pricing_mode: str):
    lines: List[BomLine] = []

    if inputs.get("openai_usage"):
        lines.extend(build_openai_lines(client, region, inputs["openai_usage"], app_name=app_name))

    if inputs.get("foundry_usage"):
        lines.extend(build_foundry_lines(client, region, inputs["foundry_usage"], app_name=app_name))

    aisrch = inputs.get("ai_search", {})
    l = _ai_search_line(client, region, aisrch.get("tier", "none"),
                       aisrch.get("replicas", 0), aisrch.get("partitions", 0), app_name)
    if l: lines.append(l)

    gpu = inputs.get("gpu_vm", {})
    if gpu.get("sku") and gpu["sku"] != "none":
        lines.extend(_gpu_vm_lines(
            client, region, arm_sku=gpu["sku"], count=gpu["count"], hours=gpu["hours"],
            os_is_windows=gpu.get("os_windows", False),
            pricing_mode=pricing_mode, app_name=app_name,
            use_ahb=bool(inputs.get("__use_ahb__", False)),
        ))

    ft = inputs.get("finetune", {})
    if ft.get("model") and ft["model"] != "none":
        l = _openai_finetune_line(client, region, ft["model"], ft.get("tokens_1k", 0.0), app_name)
        if l: lines.append(l)

    for sk, qty in (inputs.get("cognitive") or {}).items():
        l = _cognitive_line(client, region, sk, qty, app_name)
        if l: lines.append(l)

    return lines, []
