"""Thin client for the public Azure Retail Prices API.

Docs: https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices

No authentication required. Results are paginated via NextPageLink. We cache
results per OData filter to avoid repeated calls within a session.

Regional fallback: if an armRegionName-scoped filter returns zero records
and that region has a fallback mapping (e.g. malaysiacentral -> southeastasia),
the client transparently re-queries against the fallback region. The set
`fallbacks_used` records which primary→fallback swaps happened so the UI
can surface them to the user.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

import requests

from ..constants import REGION_FALLBACKS


ENDPOINT = "https://prices.azure.com/api/retail/prices"
HOURS_PER_MONTH = 730.0


# Supported billing terms. Values are the canonical mode keys used in the
# rest of the codebase. PAYG is the default.
BILLING_TERMS: Dict[str, Dict] = {
    "payg":  {"label": "Pay-as-you-go",        "hours_per_term": None, "sp_term": None, "ri_term": None},
    "sp_1y": {"label": "Savings Plan (1 year)",  "hours_per_term": None, "sp_term": "P1Y", "ri_term": None},
    "sp_3y": {"label": "Savings Plan (3 years)", "hours_per_term": None, "sp_term": "P3Y", "ri_term": None},
    "ri_1y": {"label": "Reserved Instance (1 year)",  "hours_per_term": HOURS_PER_MONTH * 12, "sp_term": None, "ri_term": "1 Year"},
    "ri_3y": {"label": "Reserved Instance (3 years)", "hours_per_term": HOURS_PER_MONTH * 36, "sp_term": None, "ri_term": "3 Years"},
}


@dataclass
class SavingsPlanEntry:
    term: str          # "P1Y" | "P3Y"
    unit_price: float
    retail_price: float


@dataclass
class PriceRecord:
    product_name: str
    sku_name: str
    meter_name: str
    arm_sku_name: str
    arm_region_name: str
    unit_price: float
    retail_price: float
    unit_of_measure: str
    currency_code: str
    price_type: str          # Consumption / Reservation / DevTestConsumption
    service_name: str
    service_family: str
    product_id: str
    sku_id: str
    meter_id: str
    raw: dict
    reservation_term: str = ""        # "1 Year" | "3 Years" | ""
    savings_plan: List[SavingsPlanEntry] = field(default_factory=list)

    @classmethod
    def from_api(cls, item: dict) -> "PriceRecord":
        sp_entries: List[SavingsPlanEntry] = []
        for sp in item.get("savingsPlan", []) or []:
            try:
                sp_entries.append(
                    SavingsPlanEntry(
                        term=sp.get("term", ""),
                        unit_price=float(sp.get("unitPrice", 0.0) or 0.0),
                        retail_price=float(sp.get("retailPrice", 0.0) or 0.0),
                    )
                )
            except (TypeError, ValueError):
                continue
        return cls(
            product_name=item.get("productName", ""),
            sku_name=item.get("skuName", ""),
            meter_name=item.get("meterName", ""),
            arm_sku_name=item.get("armSkuName", ""),
            arm_region_name=item.get("armRegionName", ""),
            unit_price=float(item.get("unitPrice", 0.0)),
            retail_price=float(item.get("retailPrice", 0.0)),
            unit_of_measure=item.get("unitOfMeasure", ""),
            currency_code=item.get("currencyCode", "USD"),
            price_type=item.get("type", ""),
            service_name=item.get("serviceName", ""),
            service_family=item.get("serviceFamily", ""),
            product_id=item.get("productId", ""),
            sku_id=item.get("skuId", ""),
            meter_id=item.get("meterId", ""),
            raw=item,
            reservation_term=item.get("reservationTerm", ""),
            savings_plan=sp_entries,
        )


class RetailPricesClient:
    def __init__(self, currency: str = "USD", timeout: float = 30.0):
        self.currency = currency
        self.timeout = timeout
        self.session = requests.Session()
        self._cache: Dict[str, List[PriceRecord]] = {}
        self._last_error: Optional[str] = None
        # Record (primary, fallback) pairs when the fallback was used so the
        # UI can inform the user that e.g. Malaysia prices came from SEA.
        self.fallbacks_used: Set[Tuple[str, str]] = set()
        # Record (arm_sku, pricing_mode) pairs where RI/SP wasn't available
        # and we fell back to PAYG for that particular SKU.
        self.term_fallbacks: Set[Tuple[str, str]] = set()

    def query(self, odata_filter: str, max_pages: int = 3) -> List[PriceRecord]:
        """Query retail prices; transparently falls back to an alternate
        region if the primary returns empty and a fallback is mapped."""
        key = f"{self.currency}|{odata_filter}|{max_pages}"
        if key in self._cache:
            return self._cache[key]

        results = self._query_raw(odata_filter, max_pages)

        # Regional fallback: if filter names a region with a fallback mapping
        # and the primary returned nothing, retry against the fallback(s).
        if not results:
            m = re.search(r"armRegionName\s+eq\s+'([a-z0-9]+)'", odata_filter, re.IGNORECASE)
            if m:
                primary = m.group(1).lower()
                for fallback in REGION_FALLBACKS.get(primary, []):
                    alt_filter = odata_filter.replace(
                        f"armRegionName eq '{primary}'",
                        f"armRegionName eq '{fallback}'",
                    )
                    alt = self._query_raw(alt_filter, max_pages)
                    if alt:
                        self.fallbacks_used.add((primary, fallback))
                        self._cache[key] = alt
                        return alt

        self._cache[key] = results
        return results

    def _query_raw(self, odata_filter: str, max_pages: int = 3) -> List[PriceRecord]:
        """Single-region query without fallback logic."""
        params = {"currencyCode": self.currency, "$filter": odata_filter}
        url: Optional[str] = ENDPOINT
        results: List[PriceRecord] = []
        pages = 0
        try:
            while url and pages < max_pages:
                if pages == 0:
                    resp = self.session.get(url, params=params, timeout=self.timeout)
                else:
                    resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("Items", []):
                    results.append(PriceRecord.from_api(item))
                url = data.get("NextPageLink") or None
                pages += 1
        except requests.RequestException as exc:
            self._last_error = f"Retail Prices API error for filter '{odata_filter}': {exc}"
            return []
        return results

    # ---------- Convenience lookups ----------

    def vm_price(
        self,
        arm_sku_name: str,
        region: str,
        os_is_windows: bool,
        pricing_mode: str = "payg",
        use_ahb: bool = False,
    ) -> Optional[PriceRecord]:
        """Price per hour for a VM SKU under the chosen billing term.

        Returns a PriceRecord whose `retail_price` is ALWAYS a per-hour
        effective rate — so downstream builders can uniformly compute
        `retail_price * count * 730` for monthly cost. For RI the API's
        prepaid total is converted to an amortized per-hour rate.

        Falls back transparently to PAYG when the requested term isn't
        available for this SKU in the region. Fallbacks are recorded in
        `self.term_fallbacks` so the UI can surface them.

        Azure Hybrid Benefit (AHB): when `use_ahb=True` AND the OS is
        Windows, the lookup queries the Linux price for the SAME ARM SKU
        (which is what Microsoft bills under AHB — you supply your own
        Windows Server license with SA). The returned record's meter_name
        is tagged ' (AHB Windows)' so callers can surface it in the BOM.

        Published Azure retail reference numbers (captured 2026-04) for
        Standard_D4s_v5 Linux in eastus — used as ground truth by
        scripts/verify_calculations.py:

            pricing_mode | monthly (× 730 h) | per-hour
            -------------|--------------------|-----------
            payg         |   ~$140.16 USD     |  $0.192
            sp_1y        |   ~$118.04 USD     |  $0.1617
            sp_3y        |   ~$89.79  USD     |  $0.123
            ri_1y        |   ~$82.12  USD     |  $0.1125 (amortized from 1Y prepaid)
            ri_3y        |   ~$49.27  USD     |  $0.0675 (amortized from 3Y prepaid)

        The verify harness tolerates ±2% drift (Microsoft publishes quarterly
        discount adjustments). If all five numbers move >3% the discount
        math or the term-fallback logic likely regressed — investigate
        before bumping the tolerance.
        """
        term_cfg = BILLING_TERMS.get(pricing_mode, BILLING_TERMS["payg"])

        # With AHB, price the Windows VM as Linux (customer brings the
        # Windows license) but keep the OS label in the returned record.
        effective_windows_for_lookup = os_is_windows and not use_ahb

        # Always fetch the Consumption record — we need it for PAYG and for
        # Savings Plan (SP entries live inside the Consumption record).
        payg = self._vm_payg(arm_sku_name, region, effective_windows_for_lookup)

        def _tag_ahb(rec: Optional[PriceRecord]) -> Optional[PriceRecord]:
            if rec is None or not (use_ahb and os_is_windows):
                return rec
            return dataclasses.replace(rec, meter_name=f"{rec.meter_name} (AHB Windows)")

        if pricing_mode == "payg" or pricing_mode is None:
            return _tag_ahb(payg)

        # Savings Plan: look inside the Consumption record's savingsPlan array.
        sp_term = term_cfg.get("sp_term")
        if sp_term:
            if not payg:
                return None
            for sp in payg.savings_plan:
                if sp.term == sp_term:
                    tagged_meter = f"{payg.meter_name} (SP {sp_term})"
                    sp_rec = dataclasses.replace(
                        payg,
                        retail_price=sp.retail_price,
                        unit_price=sp.unit_price,
                        meter_name=tagged_meter,
                        price_type="SavingsPlan",
                    )
                    return _tag_ahb(sp_rec)
            # Not available → fall back to PAYG
            self.term_fallbacks.add((arm_sku_name, pricing_mode))
            return _tag_ahb(payg)

        # Reserved Instance
        ri_term = term_cfg.get("ri_term")
        if ri_term:
            filt = (
                f"serviceName eq 'Virtual Machines' "
                f"and armRegionName eq '{region}' "
                f"and armSkuName eq '{arm_sku_name}' "
                f"and priceType eq 'Reservation'"
            )
            records = self.query(filt, max_pages=5)

            def is_windows_rec(r: PriceRecord) -> bool:
                return "windows" in r.product_name.lower()

            # With AHB on a Windows VM, use the Linux RI record.
            candidates = [
                r for r in records
                if r.reservation_term == ri_term
                and is_windows_rec(r) == effective_windows_for_lookup
            ]
            if not candidates:
                self.term_fallbacks.add((arm_sku_name, pricing_mode))
                return _tag_ahb(payg)
            chosen = min(candidates, key=lambda r: r.retail_price)
            # RI retailPrice is the TOTAL prepaid for the term. Convert to a
            # per-hour equivalent so downstream math stays identical.
            hours_per_term = term_cfg["hours_per_term"]
            per_hour = chosen.retail_price / hours_per_term if hours_per_term else 0
            ri_rec = dataclasses.replace(
                chosen,
                retail_price=per_hour,
                unit_price=per_hour,
                meter_name=f"{chosen.meter_name} (RI {ri_term})",
                price_type="Reservation",
            )
            return _tag_ahb(ri_rec)

        return _tag_ahb(payg)

    def _vm_payg(
        self, arm_sku_name: str, region: str, os_is_windows: bool
    ) -> Optional[PriceRecord]:
        filt = (
            f"serviceName eq 'Virtual Machines' "
            f"and armRegionName eq '{region}' "
            f"and armSkuName eq '{arm_sku_name}' "
            f"and priceType eq 'Consumption'"
        )
        records = self.query(filt, max_pages=5)

        def is_windows_rec(r: PriceRecord) -> bool:
            return "windows" in r.product_name.lower()

        def is_spot_or_low(r: PriceRecord) -> bool:
            m = (r.meter_name + " " + r.sku_name).lower()
            return "spot" in m or "low priority" in m

        candidates = [
            r for r in records
            if not is_spot_or_low(r) and is_windows_rec(r) == os_is_windows
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda r: r.retail_price)

    def disk_price(self, meter_name: str, region: str) -> Optional[PriceRecord]:
        filt = (
            f"serviceName eq 'Storage' "
            f"and armRegionName eq '{region}' "
            f"and meterName eq '{meter_name}'"
        )
        recs = self.query(filt)
        if not recs:
            return None
        return min(recs, key=lambda r: r.retail_price)

    def first_match(self, odata_filter: str) -> Optional[PriceRecord]:
        recs = self.query(odata_filter)
        return recs[0] if recs else None
