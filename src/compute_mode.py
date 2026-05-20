"""Mode-aware baseline defaults for non-lift-shift pillars.

When the user picks a global compute mode (`saving` / `normal` /
`high_perf` from `constants.COMPUTE_MODES`), every pillar that exposes
a tier / SKU selector should default to a tier that matches the mode —
not to "none" / 0 — so the BOM is never empty after a Generate click.

Auto-simulate (Sonnet-driven) already follows the mode via the prompt
hint. This helper covers the OFFLINE path: user turns auto-simulate
off, uploads a sparse doc, but still wants a reasonable first-pass
BOM to fine-tune.

Design:
- One function per pillar (`apply_<pillar>_baselines`) that mutates a
  prefs dict to fill empty fields. Existing user-set values are NEVER
  overwritten — we only touch keys that are missing, None, "none",
  or zero.
- A dispatcher (`apply_baselines`) calls the right pillar's helper.
- Mode-tier mapping is derived from `COMPUTE_MODES` in constants.py,
  with code comments citing the MS Learn pages each pick comes from.
"""
from __future__ import annotations

from typing import Any, Dict

from .constants import COMPUTE_MODES, DEFAULT_COMPUTE_MODE


def _is_empty(v: Any) -> bool:
    """True if a pref value should be treated as 'not set' by the user."""
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == "" or v.lower() == "none"
    if isinstance(v, (int, float)):
        return v == 0
    if isinstance(v, dict):
        return not v or all(_is_empty(x) for x in v.values())
    if isinstance(v, (list, tuple, set)):
        return len(v) == 0
    return False


def _resolve_mode(mode: str) -> str:
    return mode if mode in COMPUTE_MODES else DEFAULT_COMPUTE_MODE


# --- infra_modernization: App Service + AKS + ACR -------------------------

# App Service SKU per mode. Saving = Basic B1 (~$54/mo), Normal = P0v3 (~$56/mo
# Premium v3 entry — actually similar price to S3 but better hardware),
# HP = P2v3 (~$220/mo, 4 vCPU / 16 GB).
# Ref: https://learn.microsoft.com/en-us/azure/app-service/overview-hosting-plans
_APP_SERVICE_BY_MODE = {
    "saving": "B1",
    "normal": "P0v3",
    "high_perf": "P2v3",
}

# ACR SKU per mode. Saving = Basic ($5/mo, 10 GB). Normal = Standard ($20/mo,
# 100 GB). HP = Premium ($50/mo, 500 GB, geo-replication, content trust).
# Ref: https://learn.microsoft.com/en-us/azure/container-registry/container-registry-skus
_ACR_BY_MODE = {
    "saving": "Basic",
    "normal": "Standard",
    "high_perf": "Premium",
}


def apply_infra_modernization_baselines(mode: str, prefs: Dict[str, Any]) -> Dict[str, Any]:
    """If the user enabled the pillar but hasn't picked an App Service /
    AKS / ACR tier yet, seed a mode-appropriate baseline so the
    first-pass BOM is non-empty. Existing picks are preserved."""
    mode = _resolve_mode(mode)

    # App Service — only seed if NOTHING is set (no sku and no count).
    if _is_empty(prefs.get("app_service")):
        prefs["app_service"] = {
            "sku": _APP_SERVICE_BY_MODE[mode],
            "count": 1,
            "os_windows": False,
        }

    # ACR — auto-tick a small baseline because nearly every modernization
    # project ends up with at least one container registry.
    if _is_empty(prefs.get("acr")):
        prefs["acr"] = {
            "sku": _ACR_BY_MODE[mode],
            "geo_replica_count": 0,
        }

    return prefs


# --- data_platform: Azure SQL DB, Postgres/MySQL, Cosmos, Redis ----------

# Azure SQL DB tier per mode.
# Saving      → General Purpose Serverless 1 vCore (~$370/mo with auto-pause)
# Normal      → General Purpose provisioned 2 vCore (~$370/mo, no auto-pause)
# High Perf   → Business Critical 8 vCore (~$2,000+/mo, premium IOPS, in-mem OLTP)
# Ref: https://learn.microsoft.com/en-us/azure/azure-sql/database/service-tiers-sql-database-vcore
_AZ_SQL_BY_MODE = {
    "saving":    {"tier": "gp", "vcores": 1, "storage_gb": 32, "zone_redundant": False, "serverless": True},
    "normal":    {"tier": "gp", "vcores": 2, "storage_gb": 100, "zone_redundant": False, "serverless": False},
    "high_perf": {"tier": "bc", "vcores": 8, "storage_gb": 250, "zone_redundant": True,  "serverless": False},
}

# Postgres / MySQL Flexible per mode.
# Saving      → Burstable B1ms (~$13/mo, 1 vCore / 2 GB) — auto-pause possible
# Normal      → General Purpose D2ds_v5 (~$120/mo, 2 vCore / 8 GB)
# High Perf   → Memory-Optimised E4ds_v5 + HA (~$700/mo, 4 vCore / 32 GB)
# Ref: https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-compute
_PG_MY_BY_MODE = {
    "saving":    {"tier": "burstable",         "vcores": 1, "storage_gb": 32,  "ha_enabled": False},
    "normal":    {"tier": "general_purpose",   "vcores": 2, "storage_gb": 100, "ha_enabled": False},
    "high_perf": {"tier": "memory_optimized",  "vcores": 4, "storage_gb": 256, "ha_enabled": True},
}

# Redis Cache per mode.
# Saving      → Basic C0 (~$16/mo, 250 MB, no replica)
# Normal      → Standard C2 (~$160/mo, 2.5 GB, replica)
# High Perf   → Premium P1 (~$420/mo, 6 GB, cluster, geo-replication)
# Ref: https://learn.microsoft.com/en-us/azure/azure-cache-for-redis/cache-overview
_REDIS_BY_MODE = {
    "saving":    {"tier": "basic",    "sku": "C0"},
    "normal":    {"tier": "standard", "sku": "C2"},
    "high_perf": {"tier": "premium",  "sku": "P1"},
}


def apply_data_platform_baselines(mode: str, prefs: Dict[str, Any]) -> Dict[str, Any]:
    mode = _resolve_mode(mode)

    if _is_empty(prefs.get("azure_sql_db")):
        prefs["azure_sql_db"] = dict(_AZ_SQL_BY_MODE[mode])

    if _is_empty(prefs.get("postgres_flexible")):
        prefs["postgres_flexible"] = dict(_PG_MY_BY_MODE[mode])

    if _is_empty(prefs.get("mysql_flexible")):
        prefs["mysql_flexible"] = dict(_PG_MY_BY_MODE[mode])

    if _is_empty(prefs.get("redis_cache")):
        prefs["redis_cache"] = dict(_REDIS_BY_MODE[mode])

    return prefs


# --- ai_application: minimum baseline (chatbot pattern) ------------------

# Every AI app project ends up needing at least Azure OpenAI (or Foundry)
# + AI Search. The token counts here are deliberately conservative — user
# tunes upward via the widgets after seeing the first-pass BOM.
_OPENAI_BY_MODE = {
    "saving":    {"input_1k_per_day": 50,  "output_1k_per_day": 10},   # ~$0.50/day GPT-4o-mini
    "normal":    {"input_1k_per_day": 500, "output_1k_per_day": 100},  # ~$5/day GPT-4o
    "high_perf": {"input_1k_per_day": 5000,"output_1k_per_day": 1000}, # ~$50/day production-scale
}
_AI_SEARCH_BY_MODE = {
    "saving":    {"tier": "basic",    "replicas": 1, "partitions": 1},
    "normal":    {"tier": "standard", "replicas": 1, "partitions": 1},
    "high_perf": {"tier": "s2",       "replicas": 2, "partitions": 2},
}


# GPU heuristics — when the upload mentions training / fine-tuning /
# self-host inference / specific GPU SKUs, the AI pillar should seed a
# GPU VM baseline (Sonnet's auto-simulate prompt also does this, but the
# heuristic covers the no-API-key / auto-sim-off path). Keyword list is
# intentionally tight to avoid false positives — chat-only RAG apps that
# only mention "AI" or "model" shouldn't get an A100.
# Kept tight to avoid false positives — generic "training" matches
# "no training required", so we anchor on more specific markers:
# explicit GPU SKUs, fine-tuning verbs, self-host phrasing, named
# open-weight models that require GPU hosting.
_GPU_KEYWORDS: tuple = (
    "gpu", "cuda", "nvidia",
    "a100", "h100", "v100", "mi300",
    "fine-tun", "finetun", "pretrain",
    "self-host", "selfhost", "self host",
    "stable diffusion", "llama 70b", "llama 405b",
    "mixtral", "qwen 72b",
    "ndmsv4", "ncadsv4", "ndasrv4", "nd96", "nc24", "nc48",
    # Explicit phrases — broader contexts like "model training" stay
    # detected but bare "training" alone (which often appears as
    # negation: "no training required") doesn't.
    "model training", "training pipeline", "training job",
    "inference workload", "inference at scale",
)

# Mode → recommended GPU VM SKU + count. NDmsv4 (H100) for HP training,
# NCadsv4 (A100) for HP inference, NCasv3 (T4) for saving / dev. Sonnet
# auto-simulate may override this with a more specific pick.
_GPU_VM_BY_MODE = {
    "saving":    {"sku": "Standard_NC8as_T4_v3",     "count": 1, "hours": 100,  "os_windows": False},
    "normal":    {"sku": "Standard_NC24ads_A100_v4", "count": 1, "hours": 200,  "os_windows": False},
    "high_perf": {"sku": "Standard_ND96amsr_A100_v4","count": 1, "hours": 730,  "os_windows": False},
}


def detect_gpu_workload(*hints: Any) -> bool:
    """True when any of the provided strings / iterables mention a
    GPU-relevant token. Pass classifier signals, suggested_components,
    filename, summary — anything text-shaped — and the function flattens
    + lower-cases them for substring matching."""
    blob = " ".join(_flatten_text(h) for h in hints).lower()
    return any(tok in blob for tok in _GPU_KEYWORDS)


def _flatten_text(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, (list, tuple, set)):
        return " ".join(_flatten_text(i) for i in x)
    if isinstance(x, dict):
        return " ".join(_flatten_text(v) for v in x.values())
    return str(x)


def apply_ai_application_baselines(
    mode: str, prefs: Dict[str, Any], *, wants_gpu: bool = False,
) -> Dict[str, Any]:
    mode = _resolve_mode(mode)

    # OpenAI: seed GPT-4o-mini for saving, GPT-4o otherwise.
    ou = prefs.get("openai_usage") or {}
    if _is_empty(ou):
        default_model = "gpt-4o-mini" if mode == "saving" else "gpt-4o"
        prefs["openai_usage"] = {default_model: dict(_OPENAI_BY_MODE[mode])}

    if _is_empty(prefs.get("ai_search")):
        prefs["ai_search"] = dict(_AI_SEARCH_BY_MODE[mode])

    if wants_gpu and _is_empty(prefs.get("gpu_vm")):
        prefs["gpu_vm"] = dict(_GPU_VM_BY_MODE[mode])

    return prefs


# --- Dispatcher ----------------------------------------------------------

def apply_baselines(
    pillar: str,
    mode: str,
    prefs: Dict[str, Any],
    *gpu_hints: Any,
) -> Dict[str, Any]:
    """Dispatch to the right pillar helper. Pillars not in the table are
    untouched — they either already produce non-empty BOMs (lift-shift),
    or the per-pillar widgets handle their own defaults (security,
    hybrid_multicloud, m365_and_others).

    `gpu_hints`: classifier signals / suggested_components / filename /
    summary — passed through to `detect_gpu_workload()` so the
    `ai_application` baseline can auto-seed a GPU VM when the upload
    mentions training / fine-tuning / specific GPU SKUs.
    """
    mode = _resolve_mode(mode)
    if pillar == "ai_application":
        return apply_ai_application_baselines(
            mode, prefs, wants_gpu=detect_gpu_workload(*gpu_hints),
        )
    if pillar == "infra_modernization":
        return apply_infra_modernization_baselines(mode, prefs)
    if pillar == "data_platform":
        return apply_data_platform_baselines(mode, prefs)
    return prefs
