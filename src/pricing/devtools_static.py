"""Static reference pricing for Microsoft developer-tooling SKUs.

The Azure Retail Prices API does not reliably cover GitHub / Visual Studio
/ Azure DevOps user-licensing SKUs (they're billed through commerce
channels, not consumption meters). We use published MS list prices here
as a fallback so BOMs still include these common line items — with each
line's Assumption column explicitly noting "Static reference pricing
(2026-04)" so reviewers know to verify the current rate.

Update this file when Microsoft revises pricing; the dated comment at
the top of each entry is the reference. Source:
- https://github.com/pricing
- https://visualstudio.microsoft.com/subscriptions/
- https://azure.microsoft.com/en-us/pricing/details/devops/azure-devops-services/
"""
from __future__ import annotations

from dataclasses import dataclass


REFERENCE_DATE = "2026-04"


@dataclass
class StaticRate:
    label: str
    unit: str
    per_unit_per_month_usd: float
    note: str = ""


DEVTOOLS_RATES: dict = {
    # --- GitHub ---
    "github_enterprise": StaticRate(
        label="GitHub Enterprise Cloud",
        unit="user/month",
        per_unit_per_month_usd=21.00,
        note="Per-seat licensing; MS list price 2026-04.",
    ),
    "github_advanced_security": StaticRate(
        label="GitHub Advanced Security",
        unit="committer/month",
        per_unit_per_month_usd=49.00,
        note="Billed per unique active committer across all repositories.",
    ),
    "github_copilot_business": StaticRate(
        label="GitHub Copilot Business",
        unit="user/month",
        per_unit_per_month_usd=19.00,
        note="Per-seat. Requires GitHub Enterprise or GitHub Team.",
    ),
    "github_copilot_enterprise": StaticRate(
        label="GitHub Copilot Enterprise",
        unit="user/month",
        per_unit_per_month_usd=39.00,
        note="Per-seat. Includes chat on github.com and knowledge bases.",
    ),

    # --- Visual Studio subscriptions (standard, no MSDN Platforms) ---
    "vs_professional": StaticRate(
        label="Visual Studio Professional subscription",
        unit="user/month",
        per_unit_per_month_usd=45.00,
        note="Monthly cloud subscription. Annual and standard perpetual licensing available at different rates.",
    ),
    "vs_enterprise": StaticRate(
        label="Visual Studio Enterprise subscription",
        unit="user/month",
        per_unit_per_month_usd=250.00,
        note="Monthly cloud subscription with full MSDN benefits.",
    ),

    # --- Azure DevOps Services ---
    "azdo_basic": StaticRate(
        label="Azure DevOps Basic (beyond free 5 users)",
        unit="user/month",
        per_unit_per_month_usd=6.00,
        note="First 5 users + all Visual Studio subscribers are free.",
    ),
    "azdo_basic_test": StaticRate(
        label="Azure DevOps Basic + Test Plans",
        unit="user/month",
        per_unit_per_month_usd=52.00,
        note="Required for test-plan features.",
    ),
    "azdo_hosted_pipeline": StaticRate(
        label="Azure DevOps Hosted Parallel Job",
        unit="job/month",
        per_unit_per_month_usd=40.00,
        note="Private projects: first 1,800 min/mo on one parallel job free, then $40/job.",
    ),
    "azdo_selfhosted_pipeline": StaticRate(
        label="Azure DevOps Self-hosted Parallel Job",
        unit="job/month",
        per_unit_per_month_usd=15.00,
        note="Self-hosted agent; unlimited minutes.",
    ),
}


# --- M365 / SharePoint Premium / Copilot Studio static reference rates ---
# Live retail API coverage is patchy for these. Update when MS revises.
M365_OTHERS_RATES: dict = {
    "m365_backup": StaticRate(
        label="Microsoft 365 Backup",
        unit="GB/month",
        per_unit_per_month_usd=0.15,
        note="Protected data (Exchange + OneDrive + SharePoint + Teams).",
    ),
    "m365_archive": StaticRate(
        label="Microsoft 365 Archive",
        unit="GB/month",
        per_unit_per_month_usd=0.05,
        note="Inactive/archived content cold storage.",
    ),
    "sharepoint_premium": StaticRate(
        label="SharePoint Premium (Syntex transactions)",
        unit="transaction",
        per_unit_per_month_usd=0.05,
        note="Per document processed via Syntex content AI.",
    ),
    "copilot_studio_pack_25k": StaticRate(
        label="Copilot Studio — 25K message pack",
        unit="pack/month",
        per_unit_per_month_usd=200.00,
        note="Prepaid pack, billed through Azure subscription. MS tenant-level commit tier.",
    ),
    "copilot_studio_payg": StaticRate(
        label="Copilot Studio — pay-as-you-go message",
        unit="message",
        per_unit_per_month_usd=0.01,
        note="Simple message billing when no prepaid pack is active.",
    ),
}
