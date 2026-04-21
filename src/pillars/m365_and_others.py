"""M365 & Others pillar — Azure-purchasable items that don't fit any
workload-focused pillar.

Covers:
- **Microsoft 365 Backup** (Syntex backup for Exchange / OneDrive /
  SharePoint / Teams)
- **Microsoft 365 Archive** (inactive/archived content cold storage)
- **SharePoint Premium** (Syntex content AI transactions)
- **Copilot Studio** prepaid message packs + pay-as-you-go messages
- **Generic "other" line items** — free-form table where the user types
  any Azure-billable SaaS that isn't modelled (Confluent Cloud, Elastic
  Cloud, MongoDB Atlas, Databricks Marketplace, etc.). Accepts
  label + monthly cost + notes; no retail lookup, stamped as
  "User-provided" in the Assumption column.

Pricing source: retail feed when available, otherwise static reference
from src/pricing/devtools_static.py::M365_OTHERS_RATES with the
reference-date stamp surfaced in the Assumption column.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..models import BomLine
from ..pricing.devtools_static import M365_OTHERS_RATES, REFERENCE_DATE as _STATIC_REF_DATE
from ..pricing.retail import HOURS_PER_MONTH, RetailPricesClient
from .azure_security import _pick


PILLAR_METADATA = {
    "key": "m365_and_others",
    "label": "M365 & Others",
    "description": "Microsoft 365 Backup / Archive, SharePoint Premium (Syntex), Copilot Studio, and free-form marketplace line items.",
    "icon": "📦",
    "needs_vm_extraction": False,
    "default_strategy": None,
}


# ---------------------------------------------------------------------------
# Line builders (retail-first, static fallback)
# ---------------------------------------------------------------------------

def _retail_or_static_line(
    client: RetailPricesClient,
    region: str,
    rate_key: str,
    quantity: float,
    retail_service_name: Optional[str],
    retail_meter_hint: Optional[str],
    category: str,
    custom_name: str,
    app_name: str,
) -> Optional[BomLine]:
    if quantity <= 0:
        return None
    static = M365_OTHERS_RATES.get(rate_key)
    if not static:
        return None

    chosen = None
    if retail_service_name and retail_meter_hint:
        recs = client.query(
            f"serviceName eq '{retail_service_name}' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        )
        chosen = _pick(recs, retail_meter_hint) if recs else None

    if chosen and chosen.retail_price > 0:
        rate = chosen.retail_price
        meter = chosen.meter_name
        source = "retail-prices"
        assumption = (
            f"Retail meter: {meter}; ${rate:.4f} × {quantity:,.2f} = "
            f"${rate * quantity:,.2f}/mo"
        )
        product_id = chosen.product_id
        sku_id = chosen.sku_id
        meter_id = chosen.meter_id
        currency = chosen.currency_code
    else:
        rate = static.per_unit_per_month_usd
        meter = f"Static reference ({_STATIC_REF_DATE})"
        source = "static-reference"
        assumption = (
            f"Static reference pricing ({_STATIC_REF_DATE}); retail meter not "
            f"available for this region. ${rate:.4f} × {quantity:,.2f} "
            f"{static.unit} = ${rate * quantity:,.2f}/mo. {static.note}"
        )
        product_id = sku_id = meter_id = ""
        currency = "USD"

    return BomLine(
        category=category,
        resource=f"{static.label} — {quantity:,.1f} {static.unit}",
        sku=rate_key,
        meter=meter,
        region=region if source == "retail-prices" else "global",
        quantity=float(quantity),
        unit=static.unit,
        unit_price=rate,
        monthly_cost=round(rate * quantity, 2),
        currency=currency,
        source=source,
        product_id=product_id,
        sku_id=sku_id,
        meter_id=meter_id,
        service_name=static.label,
        custom_name=custom_name,
        assumption=assumption,
    )


def _freeform_line(label: str, monthly_cost: float, notes: str, app_name: str) -> Optional[BomLine]:
    if not label or monthly_cost <= 0:
        return None
    return BomLine(
        category="Other",
        resource=label,
        sku="user-defined",
        meter="user-defined",
        region="n/a",
        quantity=1.0,
        unit="month",
        unit_price=round(float(monthly_cost), 2),
        monthly_cost=round(float(monthly_cost), 2),
        currency="USD",
        source="user-provided",
        service_name="Other (user-provided)",
        custom_name=f"{app_name}-Other-{label[:20]}" if app_name else f"Other-{label[:20]}",
        assumption=(
            "User-provided line item — no retail lookup. "
            + (f"Notes: {notes}" if notes else "Notes: (none)")
        ),
    )


# ---------------------------------------------------------------------------
# Pillar contract
# ---------------------------------------------------------------------------

def render_inputs(st, prefs: dict, app_name: str, region: str,
                  upload_bytes: bytes, upload_name: str, profile) -> dict:
    st.caption(
        "Line items billed through Azure that don't fit the workload pillars — "
        "M365 Backup/Archive, SharePoint Premium, Copilot Studio, plus a free-"
        "form 'Others' table for any marketplace SaaS."
    )

    m_pref = prefs.get("m365", {}) or {}
    with st.expander("Microsoft 365 Backup / Archive", expanded=True):
        c1, c2 = st.columns(2)
        backup_gb = c1.number_input(
            "M365 Backup — protected GB", min_value=0.0,
            value=float(m_pref.get("backup_gb", 0.0)), step=100.0,
            key="m365_backup_gb",
            help="Exchange + OneDrive + SharePoint + Teams combined.",
        )
        archive_gb = c2.number_input(
            "M365 Archive — archived GB", min_value=0.0,
            value=float(m_pref.get("archive_gb", 0.0)), step=100.0,
            key="m365_archive_gb",
        )

    sp_pref = prefs.get("sharepoint_premium", {}) or {}
    with st.expander("SharePoint Premium (Syntex content AI)", expanded=False):
        sp_transactions = st.number_input(
            "Transactions / month (Syntex document processing)", min_value=0,
            value=int(sp_pref.get("transactions", 0)),
            step=1000, key="sp_transactions",
            help="Per document processed via Syntex content AI.",
        )

    cs_pref = prefs.get("copilot_studio", {}) or {}
    with st.expander("Copilot Studio", expanded=False):
        c1, c2 = st.columns(2)
        cs_packs = c1.number_input(
            "Prepaid message packs (25K messages each)", min_value=0,
            value=int(cs_pref.get("packs_25k", 0)),
            step=1, key="cs_packs",
        )
        cs_payg_msgs = c2.number_input(
            "Pay-as-you-go messages / month (beyond packs)", min_value=0,
            value=int(cs_pref.get("payg_messages", 0)),
            step=1000, key="cs_payg",
        )

    # --- Free-form "Others" editor ---
    others_pref = prefs.get("others", []) or []
    with st.expander("Other Azure-billable line items (free-form)", expanded=False):
        st.caption(
            "Type any Azure marketplace / Azure-billed SaaS not modelled "
            "above. Rows with a blank label or 0 cost are ignored. Stamped "
            "as 'User-provided' in the Cost Assumptions sheet."
        )
        import pandas as _pd
        default_df = _pd.DataFrame(
            others_pref if others_pref else [
                {"label": "", "monthly_cost_usd": 0.0, "notes": ""},
            ],
            columns=["label", "monthly_cost_usd", "notes"],
        )
        # Pad with empty rows so the user has space to type
        while len(default_df) < 5:
            default_df = _pd.concat(
                [default_df, _pd.DataFrame([{"label": "", "monthly_cost_usd": 0.0, "notes": ""}])],
                ignore_index=True,
            )
        edited = st.data_editor(
            default_df,
            num_rows="dynamic",
            use_container_width=True,
            key="m365_others_editor",
            column_config={
                "label": st.column_config.TextColumn("Label / product", width="medium"),
                "monthly_cost_usd": st.column_config.NumberColumn(
                    "Monthly cost (USD)", min_value=0.0, format="$%.2f", width="small",
                ),
                "notes": st.column_config.TextColumn("Notes", width="large"),
            },
        )
        others_rows = []
        for _, row in edited.iterrows():
            label = str(row.get("label", "") or "").strip()
            cost = float(row.get("monthly_cost_usd", 0) or 0)
            notes = str(row.get("notes", "") or "").strip()
            if label and cost > 0:
                others_rows.append({"label": label, "monthly_cost_usd": cost, "notes": notes})

    return {
        "m365": {
            "backup_gb": float(backup_gb),
            "archive_gb": float(archive_gb),
        },
        "sharepoint_premium": {"transactions": int(sp_transactions)},
        "copilot_studio": {
            "packs_25k": int(cs_packs),
            "payg_messages": int(cs_payg_msgs),
        },
        "others": others_rows,
    }


def build_bom(client, region: str, inputs: dict, app_name: str, pricing_mode: str):
    lines: List[BomLine] = []
    prefix = f"{app_name}-" if app_name else ""

    m365 = inputs.get("m365", {}) or {}
    backup_gb = float(m365.get("backup_gb", 0))
    archive_gb = float(m365.get("archive_gb", 0))

    l = _retail_or_static_line(
        client, region, "m365_backup", backup_gb,
        retail_service_name="Microsoft 365 Backup",
        retail_meter_hint="protected data",
        category="M365",
        custom_name=f"{prefix}M365-Backup", app_name=app_name,
    )
    if l: lines.append(l)

    l = _retail_or_static_line(
        client, region, "m365_archive", archive_gb,
        retail_service_name="Microsoft 365 Archive",
        retail_meter_hint="archive",
        category="M365",
        custom_name=f"{prefix}M365-Archive", app_name=app_name,
    )
    if l: lines.append(l)

    sp = inputs.get("sharepoint_premium", {}) or {}
    sp_tx = float(sp.get("transactions", 0))
    l = _retail_or_static_line(
        client, region, "sharepoint_premium", sp_tx,
        retail_service_name="SharePoint Premium",
        retail_meter_hint="syntex",
        category="M365",
        custom_name=f"{prefix}SharePoint-Premium", app_name=app_name,
    )
    if l: lines.append(l)

    cs = inputs.get("copilot_studio", {}) or {}
    packs = float(cs.get("packs_25k", 0))
    payg = float(cs.get("payg_messages", 0))
    l = _retail_or_static_line(
        client, region, "copilot_studio_pack_25k", packs,
        retail_service_name="Copilot Studio",
        retail_meter_hint="message pack",
        category="M365",
        custom_name=f"{prefix}Copilot-Studio-Packs", app_name=app_name,
    )
    if l: lines.append(l)
    l = _retail_or_static_line(
        client, region, "copilot_studio_payg", payg,
        retail_service_name="Copilot Studio",
        retail_meter_hint="pay-as-you-go",
        category="M365",
        custom_name=f"{prefix}Copilot-Studio-PAYG", app_name=app_name,
    )
    if l: lines.append(l)

    # Free-form "Others" rows
    for row in inputs.get("others", []) or []:
        l = _freeform_line(
            label=row.get("label", ""),
            monthly_cost=row.get("monthly_cost_usd", 0),
            notes=row.get("notes", ""),
            app_name=app_name,
        )
        if l: lines.append(l)

    return lines, []
