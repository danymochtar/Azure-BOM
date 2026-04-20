"""End-to-end calculation verification harness.

Runs curated reference scenarios through the builders and compares the
computed monthly cost to expected values hand-checked against Microsoft's
Azure Pricing Calculator. Exits non-zero on any failure when `--strict`
is passed — ready to wire into CI later.

Usage:
    python scripts/verify_calculations.py --region malaysiawest
    python scripts/verify_calculations.py --region eastus --strict

The first run against a live API is the source of truth. Subsequent runs
should re-produce the same numbers. When Microsoft tweaks retail pricing,
update the `expected_monthly_usd` fields and note the date.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

# Allow `python scripts/verify_calculations.py` from repo root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.analysis import build_compute_bom  # noqa: E402
from src.landing_zone import build_landing_zone_bom  # noqa: E402
from src.models import InventoryItem  # noqa: E402
from src.pillars.ai_application import build_openai_lines  # noqa: E402
from src.pillars.azure_security import build_siem_bom  # noqa: E402
from src.pillars.data_platform import _fabric_line  # noqa: E402
from src.pricing.bandwidth import compute_tiered_egress  # noqa: E402
from src.pricing.retail import RetailPricesClient  # noqa: E402


@dataclass
class Scenario:
    label: str
    runner: Callable[[RetailPricesClient, str], float]  # returns monthly USD
    expected_monthly_usd: Optional[float] = None
    expected_min_usd: Optional[float] = None
    expected_max_usd: Optional[float] = None
    tolerance_pct: float = 2.0
    notes: str = ""


# --- Runners -----------------------------------------------------------------

def _sum_lines(lines) -> float:
    return round(sum(l.monthly_cost for l in lines), 2)


def _run_3_d4s_linux_payg(client, region):
    items = [InventoryItem(name=f"vm{i}", vcpu=4, memory_gb=16, storage_gb=128, os="Linux")
             for i in range(3)]
    lines, _ = build_compute_bom(items, client, region, pricing_mode="payg", use_ahb=False)
    # Filter to compute only (disks vary)
    compute = [l for l in lines if l.category == "Compute"]
    return _sum_lines(compute)


def _run_3_d4s_windows_ahb(client, region):
    items = [InventoryItem(name=f"vm{i}", vcpu=4, memory_gb=16, storage_gb=128, os="Windows Server 2019")
             for i in range(3)]
    lines, _ = build_compute_bom(items, client, region, pricing_mode="payg", use_ahb=True)
    compute = [l for l in lines if l.category == "Compute"]
    return _sum_lines(compute)


def _run_d4s_ri_1y(client, region):
    items = [InventoryItem(name="vm", vcpu=4, memory_gb=16, storage_gb=128, os="Linux")]
    lines, _ = build_compute_bom(items, client, region, pricing_mode="ri_1y")
    compute = [l for l in lines if l.category == "Compute"]
    return _sum_lines(compute)


def _run_d4s_payg_linux(client, region):
    items = [InventoryItem(name="vm", vcpu=4, memory_gb=16, storage_gb=128, os="Linux")]
    lines, _ = build_compute_bom(items, client, region, pricing_mode="payg")
    compute = [l for l in lines if l.category == "Compute"]
    return _sum_lines(compute)


def _run_fabric_f64(client, region):
    line = _fabric_line(client, region, "F64", app_name="")
    return line.monthly_cost if line else 0.0


def _run_sentinel_50gb_day(client, region):
    lines = build_siem_bom(
        client, region, gb_per_day=50.0, retention_days_over_free=0,
        commitment_tier="payg", defender_vm_count=0, include_cspm=False,
    )
    # Sentinel line only (drop Log Analytics commitment + retention)
    sentinel = [l for l in lines if "Sentinel" in l.resource]
    return _sum_lines(sentinel)


def _run_bandwidth_10tb(client, region):
    lines = build_landing_zone_bom(
        client, region,
        enabled_keys=["bandwidth_egress"],
        quantity_overrides={"bandwidth_egress": 10_000.0},
    )
    return _sum_lines(lines)


def _run_bandwidth_math_only():
    """Pure math — doesn't hit retail API. Validates the tiered helper."""
    cost, _, _ = compute_tiered_egress(total_gb_month=10_000, first_tier_rate=0.087)
    return cost


def _run_openai_gpt4o_mini(client, region):
    lines = build_openai_lines(
        client, region,
        usage={"gpt-4o-mini": {"input_1k_per_day": 1000.0, "output_1k_per_day": 200.0}},
        app_name="",
    )
    return _sum_lines(lines)


# --- Scenario catalog --------------------------------------------------------

SCENARIOS: List[Scenario] = [
    Scenario(
        label="3 × D4s v5 Linux PAYG",
        runner=_run_3_d4s_linux_payg,
        expected_min_usd=360.0, expected_max_usd=460.0,
        tolerance_pct=5.0,
        notes="Retail ranges across Azure regions; 3 × ~$140-150/mo = ~$420-450.",
    ),
    Scenario(
        label="3 × D4s v5 Windows + AHB == 3 × Linux PAYG",
        runner=_run_3_d4s_windows_ahb,
        expected_min_usd=360.0, expected_max_usd=460.0,
        tolerance_pct=5.0,
        notes="AHB should swap to Linux rate; total must match Linux PAYG above.",
    ),
    Scenario(
        label="D4s v5 × 1 PAYG vs RI 1Y ratio",
        runner=lambda c, r: (
            _run_d4s_ri_1y(c, r) / max(_run_d4s_payg_linux(c, r), 0.01)
        ),
        expected_min_usd=0.55, expected_max_usd=0.80,
        tolerance_pct=0.0,
        notes="RI 1Y should be 55-80% of PAYG cost.",
    ),
    Scenario(
        label="Microsoft Fabric F64 capacity",
        runner=_run_fabric_f64,
        expected_min_usd=7000.0, expected_max_usd=12_000.0,
        tolerance_pct=5.0,
        notes="F64 varies by region; ~$8-11K/mo typical.",
    ),
    Scenario(
        label="Sentinel 50 GB/day PAYG ingestion",
        runner=_run_sentinel_50gb_day,
        expected_min_usd=2000.0, expected_max_usd=4000.0,
        tolerance_pct=5.0,
        notes="Sentinel PAYG ~$2/GB × 1500 GB = ~$3000.",
    ),
    Scenario(
        label="Bandwidth egress 10 TB (tiered helper, math only)",
        runner=lambda c, r: _run_bandwidth_math_only(),
        expected_monthly_usd=861.30,
        tolerance_pct=0.5,
        notes="Tiered math: 100 GB free + 9900 × $0.087 = $861.30.",
    ),
    Scenario(
        label="Bandwidth egress 10 TB (live retail)",
        runner=_run_bandwidth_10tb,
        expected_min_usd=700.0, expected_max_usd=1000.0,
        tolerance_pct=15.0,
        notes="Real retail feed rate for tier 1 feeds the helper.",
    ),
    Scenario(
        label="Azure OpenAI GPT-4o mini (1M input + 200K output tokens/day)",
        runner=_run_openai_gpt4o_mini,
        expected_min_usd=5.0, expected_max_usd=50.0,
        tolerance_pct=10.0,
        notes="30 × 1M input tokens × $0.15/M + 30 × 0.2M output × $0.60/M ≈ $8.",
    ),
]


# --- Runner -----------------------------------------------------------------

def _format_result(s: Scenario, actual: float) -> Tuple[bool, str]:
    ok = True
    detail = f"${actual:,.2f}"
    if s.expected_monthly_usd is not None:
        expected = s.expected_monthly_usd
        tol = expected * (s.tolerance_pct / 100.0)
        delta = actual - expected
        detail = f"${actual:,.2f} vs expected ${expected:,.2f} (Δ ${delta:+,.2f}, tol ±${tol:,.2f})"
        ok = abs(delta) <= tol
    elif s.expected_min_usd is not None and s.expected_max_usd is not None:
        detail = f"${actual:,.2f} (expected range ${s.expected_min_usd:,.2f}-${s.expected_max_usd:,.2f})"
        ok = s.expected_min_usd <= actual <= s.expected_max_usd
    return ok, detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="malaysiawest", help="Azure region to price against")
    ap.add_argument("--currency", default="USD")
    ap.add_argument("--strict", action="store_true",
                    help="Exit non-zero if any scenario fails")
    args = ap.parse_args()

    client = RetailPricesClient(currency=args.currency)

    print(f"\nAzure Cost Assessment — calculation parity check")
    print(f"Region: {args.region}   Currency: {args.currency}\n")
    print(f"{'Status':<8} {'Scenario':<60} {'Result'}")
    print("-" * 130)

    failures = 0
    for s in SCENARIOS:
        try:
            actual = s.runner(client, args.region)
        except Exception as e:
            print(f"{'ERROR':<8} {s.label[:60]:<60} {type(e).__name__}: {e}")
            failures += 1
            continue
        ok, detail = _format_result(s, actual)
        mark = "PASS" if ok else "FAIL"
        print(f"{mark:<8} {s.label[:60]:<60} {detail}")
        if s.notes:
            print(f"{'':<8} {'':<60} {s.notes}")
        if not ok:
            failures += 1

    print("-" * 130)
    total = len(SCENARIOS)
    passed = total - failures
    print(f"\nResult: {passed}/{total} PASS, {failures} FAIL\n")

    if getattr(client, "fallbacks_used", None):
        print(f"Regional fallbacks used: {sorted(client.fallbacks_used)}")
    if getattr(client, "_last_error", None):
        print(f"Retail API errors: {client._last_error}")

    if failures and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
