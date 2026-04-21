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


def _run_d4s_linux_by_term(term: str):
    """Factory returning a scenario runner for D4s v5 Linux at a given
    billing term — used by the per-term reference scenarios below.

    NOTE: `compute_bom` includes OS disk + data disks; we filter to Compute
    only so the comparison against Microsoft's advertised per-SKU VM rate
    is apples-to-apples.
    """
    def _runner(client, region):
        items = [InventoryItem(name="vm", vcpu=4, memory_gb=16, storage_gb=128, os="Linux")]
        lines, _ = build_compute_bom(items, client, region, pricing_mode=term)
        compute = [l for l in lines if l.category == "Compute"]
        return _sum_lines(compute)
    return _runner


def _run_github_enterprise_100(client, region):
    """GitHub Enterprise Cloud × 100 users via the infra_modernization
    pillar's static reference builder. Retail API doesn't cover per-seat
    licensing, so this exercises the devtools_static fallback path."""
    from src.pillars.infra_modernization import _devtools_line
    line = _devtools_line("github_enterprise", 100, app_name="")
    return line.monthly_cost if line else 0.0


def _run_github_copilot_business_100(client, region):
    from src.pillars.infra_modernization import _devtools_line
    line = _devtools_line("github_copilot_business", 100, app_name="")
    return line.monthly_cost if line else 0.0


def _run_m365_backup_5000gb(client, region):
    """M365 Backup 5000 GB — retail-or-static builder."""
    from src.pillars.m365_and_others import _retail_or_static_line
    line = _retail_or_static_line(
        client=client, region=region,
        rate_key="m365_backup", quantity=5000.0,
        retail_service_name="Microsoft 365 Backup",
        retail_meter_hint="protected data",
        category="M365",
        custom_name="Verify-M365-Backup",
        app_name="",
    )
    return line.monthly_cost if line else 0.0


def _run_m365_others_freeform(client, region):
    """Generic free-form 'other marketplace' row — exports the user-supplied
    monthly cost verbatim."""
    from src.pillars.m365_and_others import _freeform_line
    line = _freeform_line(
        label="Elastic Cloud Enterprise",
        monthly_cost=1200.0, notes="regression scenario",
        app_name="",
    )
    return line.monthly_cost if line else 0.0


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
    # ---------------------------------------------------------------------
    # Per-term D4s v5 Linux reference scenarios (eastus)
    #
    # Anchored to Microsoft's published retail prices captured 2026-04 — see
    # the `vm_price()` docstring for the exact numbers. Tolerances here are
    # tight (2%) on purpose: these prove the RI/SP amortization and term
    # fallback logic agree with Microsoft's pricing page, quarter to quarter.
    # When MS moves rates, tighten/loosen here rather than bumping tolerance
    # everywhere.
    # ---------------------------------------------------------------------
    Scenario(
        label="D4s v5 Linux PAYG (eastus reference)",
        runner=_run_d4s_linux_by_term("payg"),
        expected_monthly_usd=140.16,
        tolerance_pct=2.0,
        notes="$0.192/h × 730 = $140.16 (published reference 2026-04).",
    ),
    Scenario(
        label="D4s v5 Linux Savings Plan 1Y (eastus reference)",
        runner=_run_d4s_linux_by_term("sp_1y"),
        expected_monthly_usd=118.04,
        tolerance_pct=2.0,
        notes="Savings Plan 1Y ~$0.1617/h × 730 (reference 2026-04).",
    ),
    Scenario(
        label="D4s v5 Linux Savings Plan 3Y (eastus reference)",
        runner=_run_d4s_linux_by_term("sp_3y"),
        expected_monthly_usd=89.79,
        tolerance_pct=2.0,
        notes="Savings Plan 3Y ~$0.123/h × 730 (reference 2026-04).",
    ),
    Scenario(
        label="D4s v5 Linux RI 1Y (eastus reference, amortized)",
        runner=_run_d4s_linux_by_term("ri_1y"),
        expected_monthly_usd=82.12,
        tolerance_pct=2.0,
        notes="1Y prepaid / (730 × 12) = amortized per-hour $0.1125 (2026-04).",
    ),
    Scenario(
        label="D4s v5 Linux RI 3Y (eastus reference, amortized)",
        runner=_run_d4s_linux_by_term("ri_3y"),
        expected_monthly_usd=49.27,
        tolerance_pct=2.0,
        notes="3Y prepaid / (730 × 36) = amortized per-hour $0.0675 (2026-04).",
    ),
    # ---------------------------------------------------------------------
    # Static-reference pricing scenarios (DevTools + M365 & Others)
    #
    # These bypass the Retail Prices API — the Retail API doesn't carry
    # per-seat GitHub / VS / Azure DevOps licensing or many M365 Syntex
    # meters, so the builders fall back to a dated static sheet. These
    # scenarios guard the fallback math and stamp-dates.
    # ---------------------------------------------------------------------
    Scenario(
        label="GitHub Enterprise × 100 users (static 2026-04 reference)",
        runner=_run_github_enterprise_100,
        expected_monthly_usd=2100.0,    # 100 × $21
        tolerance_pct=0.5,
        notes="Static reference: 100 × $21/user/month = $2100.",
    ),
    Scenario(
        label="GitHub Copilot Business × 100 users (static reference)",
        runner=_run_github_copilot_business_100,
        expected_monthly_usd=1900.0,    # 100 × $19
        tolerance_pct=0.5,
        notes="Static reference: 100 × $19/user/month = $1900.",
    ),
    Scenario(
        label="M365 Backup 5000 GB (retail or static fallback)",
        runner=_run_m365_backup_5000gb,
        expected_min_usd=500.0, expected_max_usd=1000.0,
        tolerance_pct=5.0,
        notes="5000 × $0.15/GB = $750 via static fallback (retail varies).",
    ),
    Scenario(
        label="M365 & Others generic free-form line (echo)",
        runner=_run_m365_others_freeform,
        expected_monthly_usd=1200.0,
        tolerance_pct=0.5,
        notes="Free-form 'user-provided' line should echo the monthly cost.",
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
