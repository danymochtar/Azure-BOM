"""Azure OpenAI / Azure ML cost model.

No VM extraction required. User inputs per-model token volume; we look
up retail per-1K-token prices via the Retail Prices API.

Default model list is a small curated set; extend as needed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..architecture.security_tier import _pick
from ..models import BomLine
from ..pricing.retail import RetailPricesClient


@dataclass
class OpenAIModel:
    key: str
    label: str
    # substrings used to find the right input/output meter in the retail feed
    input_meter: str
    output_meter: str
    default_input_1k_per_day: float = 0.0
    default_output_1k_per_day: float = 0.0


AZURE_OPENAI_MODELS: Dict[str, OpenAIModel] = {
    "gpt-4o": OpenAIModel(
        key="gpt-4o",
        label="GPT-4o",
        input_meter="gpt-4o inp",
        output_meter="gpt-4o outp",
    ),
    "gpt-4o-mini": OpenAIModel(
        key="gpt-4o-mini",
        label="GPT-4o mini",
        input_meter="gpt-4o mini inp",
        output_meter="gpt-4o mini outp",
    ),
    "gpt-4-turbo": OpenAIModel(
        key="gpt-4-turbo",
        label="GPT-4 Turbo",
        input_meter="gpt-4 turbo inp",
        output_meter="gpt-4 turbo outp",
    ),
    "gpt-35-turbo": OpenAIModel(
        key="gpt-35-turbo",
        label="GPT-3.5 Turbo",
        input_meter="gpt-3.5 turbo inp",
        output_meter="gpt-3.5 turbo outp",
    ),
    "text-embedding-3-large": OpenAIModel(
        key="text-embedding-3-large",
        label="Embedding v3 (large)",
        input_meter="text-embedding-3 large",
        output_meter="text-embedding-3 large",
    ),
}


def _openai_meter_price(
    client: RetailPricesClient, region: str, meter_hint: str
) -> Optional[float]:
    recs = client.query(
        f"serviceName eq 'Cognitive Services' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    if not recs:
        recs = client.query(
            f"serviceFamily eq 'AI + Machine Learning' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        )
    chosen = _pick(recs, meter_hint)
    return chosen.retail_price if chosen else None


def build_ai_ml_bom(
    client: RetailPricesClient,
    region: str,
    usage: Dict[str, Dict[str, float]],
    app_name: str = "",
) -> List[BomLine]:
    """`usage` is `{model_key: {"input_1k_per_day": X, "output_1k_per_day": Y}}`.

    1K-token units are multiplied by 30 to get per-month quantity.
    Embedding models use only the input side.
    """
    lines: List[BomLine] = []
    for model_key, vol in usage.items():
        model = AZURE_OPENAI_MODELS.get(model_key)
        if model is None:
            continue
        input_1k_month = round(vol.get("input_1k_per_day", 0) * 30, 2)
        output_1k_month = round(vol.get("output_1k_per_day", 0) * 30, 2)

        if input_1k_month > 0:
            price = _openai_meter_price(client, region, model.input_meter)
            lines.append(_line(
                region, model, "input", input_1k_month, price,
                app_name, currency="USD",
            ))
        if output_1k_month > 0 and model.output_meter != model.input_meter:
            price = _openai_meter_price(client, region, model.output_meter)
            lines.append(_line(
                region, model, "output", output_1k_month, price,
                app_name, currency="USD",
            ))
    return lines


def _line(region, model, side, qty_1k, price, app_name, currency):
    priced = price is not None and price > 0
    return BomLine(
        category="AI + ML",
        resource=f"Azure OpenAI {model.label} — {side} tokens",
        sku=model.key,
        meter=f"{model.label} {side}",
        region=region,
        quantity=qty_1k,
        unit="1K tokens",
        unit_price=price or 0.0,
        monthly_cost=round((price or 0.0) * qty_1k, 2),
        currency=currency,
        source="retail-prices" if priced else "retail-prices-miss",
        service_name="Azure OpenAI Service",
        custom_name=f"{app_name}-OpenAI-{model.key}" if app_name else f"OpenAI-{model.key}",
    )
