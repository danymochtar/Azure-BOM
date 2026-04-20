"""AI Application & Development pillar.

Azure OpenAI per-token pricing + AI Search + GPU VMs for training/fine-tuning
+ Cognitive Services stub + OpenAI fine-tuning training tokens.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..models import BomLine, InventoryItem
from ..pricing.retail import HOURS_PER_MONTH, RetailPricesClient
from .azure_security import _pick


PILLAR_METADATA = {
    "key": "ai_application",
    "label": "AI Application & Development",
    "description": "Azure OpenAI, AI Search, ML workspace, GPU fine-tuning, Cognitive Services.",
    "icon": "🤖",
    "needs_vm_extraction": False,
    "default_strategy": None,
}


# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------

@dataclass
class OpenAIModel:
    key: str
    label: str
    input_meter: str
    output_meter: str


AZURE_OPENAI_MODELS: Dict[str, OpenAIModel] = {
    "gpt-4o":                 OpenAIModel("gpt-4o",                 "GPT-4o",              "gpt-4o inp",            "gpt-4o outp"),
    "gpt-4o-mini":            OpenAIModel("gpt-4o-mini",            "GPT-4o mini",         "gpt-4o mini inp",       "gpt-4o mini outp"),
    "gpt-4-turbo":            OpenAIModel("gpt-4-turbo",            "GPT-4 Turbo",         "gpt-4 turbo inp",       "gpt-4 turbo outp"),
    "gpt-35-turbo":           OpenAIModel("gpt-35-turbo",           "GPT-3.5 Turbo",       "gpt-3.5 turbo inp",     "gpt-3.5 turbo outp"),
    "text-embedding-3-large": OpenAIModel("text-embedding-3-large", "Embedding v3 (large)", "text-embedding-3 large", "text-embedding-3 large"),
}


AI_SEARCH_TIERS: Dict[str, Dict] = {
    "basic":    {"label": "Basic",             "meter_hint": "basic"},
    "standard": {"label": "S1 Standard",       "meter_hint": "s1 standard"},
    "s2":       {"label": "S2",                "meter_hint": "s2"},
    "s3":       {"label": "S3 (High Density)", "meter_hint": "s3"},
    "l1":       {"label": "L1 (Storage Opt.)", "meter_hint": "l1"},
    "l2":       {"label": "L2 (Storage Opt.)", "meter_hint": "l2"},
}


GPU_VM_SKUS: Dict[str, str] = {
    # Reasonable defaults; users can pick a different SKU in the number_input
    "Standard_NC4as_T4_v3":  "NCas T4 v3 (4 vCPU, 1× T4)",
    "Standard_NC8as_T4_v3":  "NCas T4 v3 (8 vCPU, 1× T4)",
    "Standard_NC24ads_A100_v4": "NCads A100 v4 (24 vCPU, 1× A100 80GB)",
    "Standard_NC48ads_A100_v4": "NCads A100 v4 (48 vCPU, 2× A100 80GB)",
    "Standard_ND96asr_v4":   "ND A100 v4 (96 vCPU, 8× A100 40GB)",
}


# ---------------------------------------------------------------------------
# Line helpers
# ---------------------------------------------------------------------------

def _openai_meter_price(client: RetailPricesClient, region: str, meter_hint: str) -> Optional[float]:
    recs = client.query(
        f"serviceName eq 'Cognitive Services' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    if not recs:
        recs = client.query(
            f"serviceFamily eq 'AI + Machine Learning' and armRegionName eq '{region}' and priceType eq 'Consumption'"
        )
    chosen = _pick(recs, meter_hint)
    return chosen.retail_price if chosen else None


def _openai_token_line(region, model, side, qty_1k, price, app_name, currency="USD") -> BomLine:
    priced = price is not None and price > 0
    return BomLine(
        category="AI + ML",
        resource=f"Azure OpenAI {model.label} — {side} tokens",
        sku=model.key,
        meter=f"{model.label} {side}",
        region=region,
        quantity=qty_1k, unit="1K tokens",
        unit_price=price or 0.0,
        monthly_cost=round((price or 0.0) * qty_1k, 2),
        currency=currency,
        source="retail-prices" if priced else "retail-prices-miss",
        service_name="Azure OpenAI Service",
        custom_name=f"{app_name}-OpenAI-{model.key}" if app_name else f"OpenAI-{model.key}",
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
            p = _openai_meter_price(client, region, model.input_meter)
            lines.append(_openai_token_line(region, model, "input", input_1k_month, p, app_name))
        if output_1k_month > 0 and model.output_meter != model.input_meter:
            p = _openai_meter_price(client, region, model.output_meter)
            lines.append(_openai_token_line(region, model, "output", output_1k_month, p, app_name))
    return lines


def _ai_search_line(
    client: RetailPricesClient, region: str, tier_key: str, replicas: int, partitions: int, app_name: str,
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
        sku=chosen.sku_name or chosen.product_name,
        meter=chosen.meter_name,
        region=region,
        quantity=round(qty, 2), unit="search-unit-hours",
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
    arm_sku: str, count: int, hours: float,
    os_is_windows: bool, pricing_mode: str, app_name: str,
) -> List[BomLine]:
    if count <= 0 or hours <= 0:
        return []
    price = client.vm_price(arm_sku, region, os_is_windows=os_is_windows, pricing_mode=pricing_mode)
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
    chosen = _pick(recs, f"{model.label.lower()} fine-tun")
    if not chosen:
        chosen = _pick(recs, "fine-tun")
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


# Back-compat alias
def build_ai_ml_bom(client, region, usage, app_name=""):
    return build_openai_lines(client, region, usage, app_name)


# ---------------------------------------------------------------------------
# Pillar contract
# ---------------------------------------------------------------------------

def render_inputs(st, prefs: dict, app_name: str, region: str,
                  upload_bytes: bytes, upload_name: str, profile) -> dict:
    st.caption("Enter per-model token volume, AI Search sizing, and optional GPU fine-tuning.")

    with st.expander("Azure OpenAI token volume", expanded=True):
        st.caption("Daily token volume in 1K-token units. Leave 0 to skip.")
        usage: Dict[str, Dict[str, float]] = {}
        for mk, model in AZURE_OPENAI_MODELS.items():
            c1, c2, c3 = st.columns([2, 1, 1])
            c1.markdown(f"**{model.label}** (`{mk}`)")
            in_1k = c2.number_input("Input 1K/day", min_value=0.0, value=0.0, step=100.0,
                                    key=f"ai_in_{mk}", label_visibility="collapsed")
            out_1k = c3.number_input("Output 1K/day", min_value=0.0, value=0.0, step=50.0,
                                     key=f"ai_out_{mk}", label_visibility="collapsed")
            if in_1k > 0 or out_1k > 0:
                usage[mk] = {"input_1k_per_day": in_1k, "output_1k_per_day": out_1k}

    with st.expander("Azure AI Search (vector / semantic)", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            tier = st.selectbox(
                "Service tier",
                ["none"] + list(AI_SEARCH_TIERS.keys()),
                format_func=lambda k: "None" if k == "none" else AI_SEARCH_TIERS[k]["label"],
            )
        with c2:
            replicas = st.number_input("Replicas", min_value=0, value=0, step=1,
                                        disabled=(tier == "none"))
        with c3:
            partitions = st.number_input("Partitions", min_value=0, value=0, step=1,
                                          disabled=(tier == "none"))

    with st.expander("GPU VM (training / fine-tuning hosts)", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            gpu_sku = st.selectbox(
                "GPU SKU",
                ["none"] + list(GPU_VM_SKUS),
                format_func=lambda k: "None" if k == "none" else GPU_VM_SKUS[k],
            )
        with c2:
            gpu_count = st.number_input("Instances", min_value=0, value=0, step=1,
                                          disabled=(gpu_sku == "none"))
        with c3:
            gpu_hours = st.number_input("Hours/month", min_value=0.0, value=0.0, step=10.0,
                                          disabled=(gpu_sku == "none"))
        with c4:
            gpu_os = st.selectbox("OS", ["Linux", "Windows"], disabled=(gpu_sku == "none"))

    with st.expander("Azure OpenAI fine-tuning training", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            ft_model = st.selectbox(
                "Base model",
                ["none"] + list(AZURE_OPENAI_MODELS),
                format_func=lambda k: "None" if k == "none" else AZURE_OPENAI_MODELS[k].label,
            )
        with c2:
            ft_tokens = st.number_input("Training tokens (1K)", min_value=0.0, value=0.0, step=100.0,
                                          disabled=(ft_model == "none"))

    return {
        "openai_usage": usage,
        "ai_search": {"tier": tier, "replicas": int(replicas), "partitions": int(partitions)},
        "gpu_vm": {
            "sku": gpu_sku, "count": int(gpu_count),
            "hours": float(gpu_hours), "os_windows": (gpu_os == "Windows") if gpu_sku != "none" else False,
        },
        "finetune": {"model": ft_model, "tokens_1k": float(ft_tokens)},
    }


def build_bom(client, region: str, inputs: dict, app_name: str, pricing_mode: str):
    lines: List[BomLine] = []

    if inputs.get("openai_usage"):
        lines.extend(build_openai_lines(client, region, inputs["openai_usage"], app_name=app_name))

    aisrch = inputs.get("ai_search", {})
    l = _ai_search_line(
        client, region,
        tier_key=aisrch.get("tier", "none"),
        replicas=aisrch.get("replicas", 0),
        partitions=aisrch.get("partitions", 0),
        app_name=app_name,
    )
    if l:
        lines.append(l)

    gpu = inputs.get("gpu_vm", {})
    if gpu.get("sku") and gpu["sku"] != "none":
        lines.extend(_gpu_vm_lines(
            client, region,
            arm_sku=gpu["sku"], count=gpu["count"], hours=gpu["hours"],
            os_is_windows=gpu.get("os_windows", False),
            pricing_mode=pricing_mode, app_name=app_name,
        ))

    ft = inputs.get("finetune", {})
    if ft.get("model") and ft["model"] != "none":
        l = _openai_finetune_line(
            client, region,
            model_key=ft["model"], training_1k_tokens=ft.get("tokens_1k", 0.0), app_name=app_name,
        )
        if l:
            lines.append(l)

    return lines, []
