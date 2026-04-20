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

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

import requests

from ..constants import REGION_FALLBACKS


ENDPOINT = "https://prices.azure.com/api/retail/prices"
HOURS_PER_MONTH = 730.0


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

    @classmethod
    def from_api(cls, item: dict) -> "PriceRecord":
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
        reservation: str = "Consumption",
    ) -> Optional[PriceRecord]:
        """Price per hour for a VM SKU in a region. Filters out Low Priority
        / Spot / Windows vs Linux correctly.
        """
        filt = (
            f"serviceName eq 'Virtual Machines' "
            f"and armRegionName eq '{region}' "
            f"and armSkuName eq '{arm_sku_name}' "
            f"and priceType eq '{reservation}'"
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
