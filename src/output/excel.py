"""Render the BOM to a multi-sheet Excel workbook."""
from __future__ import annotations

import io
from typing import List

import pandas as pd

from ..models import BomLine


def build_excel_bom(
    lines: List[BomLine],
    mapping_rows: List[dict],
    region: str,
    currency: str,
) -> bytes:
    """Return bytes of an .xlsx workbook."""
    df = pd.DataFrame([l.to_row() for l in lines])
    if df.empty:
        df = pd.DataFrame(columns=[
            "category", "resource", "sku", "meter", "region", "quantity",
            "unit", "unit_price", "monthly_cost", "currency", "source",
            "product_id", "sku_id", "meter_id",
        ])

    # Numeric coercion
    for col in ("quantity", "unit_price", "monthly_cost"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["annual_cost"] = (df["monthly_cost"] * 12).round(2)

    total_monthly = round(df["monthly_cost"].sum(), 2)
    total_annual = round(df["annual_cost"].sum(), 2)
    by_cat = (
        df.groupby("category", as_index=False)[["monthly_cost", "annual_cost"]]
        .sum()
        .sort_values("monthly_cost", ascending=False)
    )

    summary = pd.DataFrame([
        {"Metric": "Region", "Value": region},
        {"Metric": "Currency", "Value": currency},
        {"Metric": "Total Resources (line items)", "Value": len(df)},
        {"Metric": "Total Monthly Cost", "Value": total_monthly},
        {"Metric": "Total Annual Cost (Monthly x 12)", "Value": total_annual},
    ])

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as xw:
        summary.to_excel(xw, sheet_name="Summary", index=False)
        by_cat.to_excel(xw, sheet_name="Summary", index=False, startrow=len(summary) + 3)

        _write_tab(df, xw, "Compute", "Compute")
        _write_tab(df, xw, "Storage", "Storage")
        _write_tab(df, xw, "Networking", "Networking")
        _write_tab(df, xw, "Security", "Security")
        _write_tab(df, xw, "Management", "Management")

        df.to_excel(xw, sheet_name="All Line Items", index=False)

        if mapping_rows:
            pd.DataFrame(mapping_rows).to_excel(xw, sheet_name="VM Mapping", index=False)

        _autosize(xw)

    return buf.getvalue()


def _write_tab(df: pd.DataFrame, xw, sheet: str, category: str) -> None:
    sub = df[df["category"] == category]
    if sub.empty:
        pd.DataFrame([{"note": f"No {category} items"}]).to_excel(xw, sheet_name=sheet, index=False)
    else:
        sub.to_excel(xw, sheet_name=sheet, index=False)


def _autosize(xw) -> None:
    for sheet_name, worksheet in xw.sheets.items():
        # xlsxwriter doesn't offer cheap autosize; set a reasonable width
        worksheet.set_column(0, 20, 22)
