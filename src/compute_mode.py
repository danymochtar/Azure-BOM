"""Mode-aware baseline defaults for non-lift-shift pillars.

Also hosts the Fabric Capacity Estimator port (see `recommend_fabric_capacity`)
which mirrors Microsoft's official estimator at
https://www.microsoft.com/en-us/microsoft-fabric/capacity-estimator
using data-size + batch-frequency + workload-mix heuristics derived from
MS Learn sizing guidance.

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

import math
import re
from typing import Any, Dict, List, Tuple

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


# Data platform pattern detection — MS Learn positioning is explicit:
# **analytical workloads** (Fabric / Synapse / Power BI / Lakehouse / ETL
# pipelines / data warehouse) and **transactional databases** (Azure
# SQL DB / Postgres / MySQL / Cosmos) are different products and should
# NOT auto-seed each other. A spec that says "Fabric Lakehouse + Power
# BI dashboards" should not produce a BOM line for MySQL Flexible.
#
# Ref:
#   https://learn.microsoft.com/en-us/fabric/fundamentals/microsoft-fabric-overview
#   https://learn.microsoft.com/en-us/azure/architecture/data-guide/big-data/
#   https://learn.microsoft.com/en-us/azure/architecture/data-guide/relational-data/online-transaction-processing
_ANALYTICS_KEYWORDS: tuple = (
    "fabric", "lakehouse", "power bi", "pbi", "synapse", "databricks",
    "etl ", "elt ", " etl", " elt", "data warehouse", "data mart",
    "medallion", "dataflow", "data factory", "adf",
    "delta lake", "parquet", "spark ", "notebook",
    "olap", "analytics", "analytical", "bi solution", "dashboard",
    "dwu", "capacity unit", "f-sku", "fabric sku", "ssas",
    "lookups", "aggregat",
)
_OLTP_KEYWORDS: tuple = (
    "oltp", "transactional", "online transaction", "crud ",
    "application database", "app db", "record management",
    "real-time write", "high-throughput write", "key-value",
    "session store", "shopping cart", "user account",
)
_SQL_MIGRATION_KEYWORDS: tuple = (
    "sql server", "ssis", "ssrs", "ssas", "stored procedure",
    "stored proc", "t-sql", "tsql", "sql migration", "azure sql",
)
_CACHE_KEYWORDS: tuple = (
    "redis", " cache ", "session store", "rate limit", "pub/sub",
    "pubsub",
)
_COSMOS_KEYWORDS: tuple = (
    "cosmos", "document db", "documentdb", "global distribution",
    "multi-master", "multi-region write",
)


# ADLS Gen2 baseline per mode — analytics workloads always need lake
# storage. Sized conservatively; user tunes upward.
_ADLS_BY_MODE = {
    "saving":    {"tier": "hot",  "redundancy": "LRS", "storage_gb": 100},
    "normal":    {"tier": "hot",  "redundancy": "LRS", "storage_gb": 1_000},
    "high_perf": {"tier": "hot",  "redundancy": "GRS", "storage_gb": 10_000},
}

# Default Fabric F-SKU when no PBI user count is known.
_FABRIC_BY_MODE = {
    "saving":    "F2",   # ~$262/mo entry — fine for ≤25 users
    "normal":    "F8",   # ~$1,050/mo — 50-100 users (Microsoft's "small org" default)
    "high_perf": "F32",  # ~$4,200/mo — 200-500 users + advanced features
}

# Power BI user count baseline used to drive Fabric F-SKU when nothing
# else is in the doc.
_PBI_USERS_BY_MODE = {"saving": 10, "normal": 50, "high_perf": 250}


def _detect_data_workload(*hints: Any) -> Dict[str, bool]:
    """Classify the data-platform workload pattern from text hints.
    Returns a dict of booleans — multiple can be True simultaneously
    (a doc may want both analytics AND a transactional database)."""
    blob = " ".join(_flatten_text(h) for h in hints).lower()
    return {
        "analytics":     any(tok in blob for tok in _ANALYTICS_KEYWORDS),
        "oltp":          any(tok in blob for tok in _OLTP_KEYWORDS),
        "sql_migration": any(tok in blob for tok in _SQL_MIGRATION_KEYWORDS),
        "cache":         any(tok in blob for tok in _CACHE_KEYWORDS),
        "cosmos":        any(tok in blob for tok in _COSMOS_KEYWORDS),
        "pg":            "postgres" in blob or "postgresql" in blob,
        "mysql":         "mysql" in blob,
    }


# --- Fabric Capacity Estimator (port of MS official tool) ----------------
#
# Heuristics derived from the official Fabric Capacity Estimator at
# https://www.microsoft.com/en-us/microsoft-fabric/capacity-estimator
# and MS Learn sizing guidance. NOT a 1:1 numeric reproduction (the
# official tool runs benchmark workloads under the hood) — instead a
# defensible approximation good enough to seed a first-pass F-SKU when
# the doc gives ANY of: data size, batch cycles, table count, workload
# mix, PBI users. Missing inputs default to conservative values.
#
# Available F-SKUs (Microsoft's Fabric SKU ladder; pay-go pricing).
_FABRIC_F_SKUS: Tuple[int, ...] = (2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048)

# Fabric "workload" tokens — match against text hints to figure out which
# workloads the user plans to enable. Each adds CU per the heuristic
# below. Keep keys aligned with the official estimator's checkbox names.
_FABRIC_WORKLOADS: Dict[str, Tuple[str, ...]] = {
    "data_factory":      ("data factory", "adf", "pipeline", "dataflow", "etl ", " etl", "elt"),
    # `spark_jobs` requires EXPLICIT Spark mention — "lakehouse" alone is
    # the storage paradigm and may not imply Spark Jobs at all (Fabric
    # Lakehouse can be queried via SQL endpoint without Spark).
    "spark_jobs":        ("spark ", "pyspark", "spark notebook", "scala notebook"),
    "data_warehouse":    ("data warehouse", "warehouse ", "dwh", "synapse warehouse", "dwu"),
    "adhoc_sql":         ("adhoc sql", "ad-hoc sql", "ad hoc sql", "interactive query"),
    "data_science":      ("data science", "ml ", "mlflow", "machine learning workspace"),
    "power_bi":          ("power bi", "pbi", "dashboard", "report ", "bi solution"),
    "power_bi_embedded": ("power bi embedded", "pbi embedded", "embedded analytics"),
    "eventstream":       ("eventstream", "event stream", "event grid", "real-time stream"),
    "eventhouse":        ("eventhouse", "kusto", "kql ", "real-time analytics"),
    "activator":         ("activator", "reflex", "data activator"),
    "sql_in_fabric":     ("sql database in fabric", "sql in fabric", "fabric sql"),
}


def _parse_data_size_gb(text: str) -> float | None:
    """Extract a data-size mention in GB. Supports MB / GB / TB / PB.
    Returns the FIRST size mentioned (closest to top of doc usually
    reflects the headline figure)."""
    # eg "10GB", "10 GB", "10 GiB", "1.5 TB", "2 petabytes"
    pat = re.compile(
        r"(\d+(?:[\.,]\d+)?)\s*(petabyte|terabyte|gigabyte|megabyte|pb|tb|gib|gb|mb)s?\b",
        re.IGNORECASE,
    )
    multipliers = {
        "mb": 1 / 1024, "megabyte": 1 / 1024,
        "gb": 1.0, "gib": 1.0, "gigabyte": 1.0,
        "tb": 1024.0, "terabyte": 1024.0,
        "pb": 1024.0 * 1024, "petabyte": 1024.0 * 1024,
    }
    m = pat.search(text)
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    return val * multipliers[m.group(2).lower()]


def _parse_batches_per_day(text: str) -> int | None:
    """Extract daily batch frequency. Recognises 'hourly', 'daily',
    'X times per day', 'X batches', 'every X minutes', 'real-time'."""
    low = text.lower()
    if "real-time" in low or "realtime" in low or "real time" in low:
        return 144  # treat as ~10-minute micro-batches → 144 batches / day
    if "hourly" in low or "every hour" in low:
        return 24
    if "every 30 min" in low or "twice an hour" in low:
        return 48
    if "every 15 min" in low:
        return 96
    m = re.search(r"every\s+(\d+)\s*(min|minute)", low)
    if m:
        return max(1, 24 * 60 // int(m.group(1)))
    m = re.search(r"(\d+)\s*(?:times|runs|cycles|batches)\s*(?:per|/)?\s*day", low)
    if m:
        return int(m.group(1))
    if "daily" in low or "once a day" in low or "nightly" in low:
        return 1
    return None


def _parse_table_count(text: str) -> int | None:
    """Extract 'X tables' count."""
    m = re.search(r"(\d+)\s*(?:to\s*\d+\s*)?tables?", text.lower())
    if m:
        return int(m.group(1))
    # "2-3 tables" pattern
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*tables?", text.lower())
    if m:
        return int(m.group(2))  # upper bound
    return None


def _parse_pbi_users(text: str) -> int | None:
    """Extract Power BI user count from phrases like 'X users', 'X analysts',
    'X concurrent users', 'serving X people'."""
    low = text.lower()
    patterns = [
        r"(\d+)\s+(?:concurrent\s+)?(?:bi\s+)?users?",
        r"(\d+)\s+(?:bi\s+)?(?:analysts?|consumers?|viewers?|readers?)",
        r"serving\s+(\d+)",
        r"(\d+)\s+power\s*bi\s+users?",
        # "concurrent user load of X", "user load of X", "load of X users"
        r"(?:concurrent\s+)?user\s+load\s+of\s+(\d+)",
        r"load\s+of\s+(\d+)\s+(?:users?|people)",
    ]
    for p in patterns:
        m = re.search(p, low)
        if m:
            return int(m.group(1))
    return None


def _detect_fabric_workloads(text: str) -> List[str]:
    low = text.lower()
    return [
        wl for wl, kws in _FABRIC_WORKLOADS.items()
        if any(k in low for k in kws)
    ]


def recommend_fabric_capacity(
    data_gb_compressed: float = 100.0,
    daily_batches: int = 1,
    num_tables: int = 10,
    workloads: List[str] | None = None,
    pbi_users: int = 25,
) -> Tuple[float, str, str]:
    """Return (estimated_cu, recommended_sku, rationale).

    Heuristic mirrors MS Fabric Capacity Estimator outputs:
      - Ingestion CU = 1 + log10(data_gb) × 2, scaled by daily_batches/4
      - Storage CU   = num_tables × 0.05 (concurrent metadata ops)
      - Workload CU adders per the official checkbox list
      - PBI user CU  = pbi_users × 0.05 (typical concurrent rendering)
      - Floor 2 CU (F2 minimum to deploy Fabric)
      - Round UP to nearest F-SKU rung
    """
    wl_set = set(workloads or [])
    cu_breakdown: List[str] = []

    # Ingestion / processing — driven by daily volume (GB/day), not
    # by batch count separately. 2 CU per decade of GB/day matches the
    # official estimator's "Data Factory pipeline" CU output for typical
    # CSV/Parquet ingestion. Examples:
    #   10  GB/day → 2 CU,  100 GB/day → 4 CU,  1 TB/day → 6 CU,
    #   10 TB/day → 8 CU, 100 TB/day → 10 CU,  1 PB/day → 12 CU
    data_per_day = max(1.0, data_gb_compressed * daily_batches)
    ingest = math.log10(data_per_day) * 2.0
    cu_breakdown.append(
        f"ingest({data_gb_compressed:.0f} GB × {daily_batches}/d = "
        f"{data_per_day:.0f} GB/day) = {ingest:.1f}"
    )

    storage = num_tables * 0.05
    cu_breakdown.append(f"tables({num_tables}) = {storage:.1f}")

    total = ingest + storage

    # Workload adders (per the official estimator's checkbox UI). Spark
    # is sized at 1 CU per 500 GB (not per 100 GB) — Spark is bursty so
    # the always-on CU budget should reflect typical 20% utilisation,
    # not 100%. Reservers tune upward via the F-SKU widget if their
    # Spark jobs are continuous.
    adders = {
        "data_factory":      max(1.0, daily_batches * 0.25),     # orchestration
        "spark_jobs":        max(4.0, data_gb_compressed / 500), # bursty, 20% avg
        "data_warehouse":    max(2.0, num_tables * 0.1),
        "adhoc_sql":         2.0,
        "data_science":      4.0,
        "power_bi":          max(2.0, pbi_users * 0.05),
        "power_bi_embedded": 4.0,
        "eventstream":       2.0,
        "eventhouse":        4.0,                                 # KQL DB sustained
        "activator":         1.0,
        "sql_in_fabric":     4.0,
    }
    for wl, cu in adders.items():
        if wl in wl_set:
            total += cu
            cu_breakdown.append(f"{wl} = {cu:.1f}")

    total = max(2.0, total)
    chosen = next((s for s in _FABRIC_F_SKUS if s >= total), _FABRIC_F_SKUS[-1])
    rationale = (
        f"Fabric Capacity Estimator: {' + '.join(cu_breakdown)} = "
        f"~{total:.1f} CU → F{chosen}"
    )
    return total, f"F{chosen}", rationale


def apply_data_platform_baselines(
    mode: str, prefs: Dict[str, Any], *hints: Any,
) -> Dict[str, Any]:
    """Seed data-platform baselines per the DETECTED workload pattern.

    No more shotgun-seeding of every database type — that produced
    nonsensical BOMs like "Fabric spec → MySQL Flexible line item".
    Instead we read the upload's signals + filename + summary and
    route to the right MS service:

      - **Analytics signal** (Fabric / Power BI / Lakehouse / ETL /
        Synapse / Databricks) → Fabric F-SKU + ADLS Gen2 lakehouse.
        Skip OLTP databases entirely unless those are ALSO mentioned.

      - **SQL migration signal** (SSIS / T-SQL / stored procedure / SQL
        Server) → Azure SQL DB at the mode's tier. SQL MI option
        deferred (user can pick via widget).

      - **OLTP signal** (transactional / CRUD / application database)
        WITHOUT a more specific DB engine mention → Postgres Flexible
        (open-source, cheaper than Azure SQL DB at small scale).

      - **PostgreSQL / MySQL mention** → Flexible Server in that engine.
        Both mentioned → both seeded.

      - **Cosmos signal** → Cosmos Serverless (saving) / autoscale
        (normal/HP). Driver-side selection.

      - **Cache signal** → Redis at the mode's tier.

      - **No signal at all** (data_platform pillar enabled with sparse
        upload) → conservative analytics baseline (Fabric F2 + 100 GB
        ADLS) since that's the most common 2026 data-platform pattern.
        User can fine-tune via widgets.
    """
    mode = _resolve_mode(mode)
    pat = _detect_data_workload(*hints)
    blob = " ".join(_flatten_text(h) for h in hints)

    seeded_analytics = False
    if pat["analytics"] or not (pat["oltp"] or pat["sql_migration"] or pat["pg"] or pat["mysql"] or pat["cosmos"] or pat["cache"]):
        # Analytics path (or fall-through default when no signal).
        # Use the Fabric Capacity Estimator port to size the F-SKU from
        # whatever the doc gave us — falls back to conservative defaults
        # field-by-field where the doc is silent. Defaults are biased
        # SMALL so a "POC limited to 2-3 tables" doc doesn't get sized
        # at production scale just because the global mode is "normal".
        # Reviewers tune upward in the widget.
        data_gb = _parse_data_size_gb(blob) or {"saving": 10.0, "normal": 100.0, "high_perf": 1000.0}[mode]
        batches = _parse_batches_per_day(blob) or 1
        tables  = _parse_table_count(blob) or {"saving": 5, "normal": 10, "high_perf": 50}[mode]
        pbi     = _parse_pbi_users(blob) or _PBI_USERS_BY_MODE[mode]
        wls     = _detect_fabric_workloads(blob)
        if not wls:
            # No explicit workload mention — assume the minimal pair
            # (Data Factory + Power BI) so the F-SKU isn't estimated
            # assuming a bare lakehouse.
            wls = ["data_factory", "power_bi"]

        cu, sku, rationale = recommend_fabric_capacity(
            data_gb_compressed=data_gb,
            daily_batches=batches,
            num_tables=tables,
            workloads=wls,
            pbi_users=pbi,
        )

        if _is_empty(prefs.get("fabric_sku")):
            prefs["fabric_sku"] = sku
        if _is_empty(prefs.get("pbi_users")):
            prefs["pbi_users"] = pbi
        if _is_empty(prefs.get("adls_gen2")):
            # Size ADLS off the actual data figure (or mode default).
            prefs["adls_gen2"] = {
                "tier": "hot",
                "redundancy": "GRS" if mode == "high_perf" else "LRS",
                "storage_gb": max(int(data_gb), _ADLS_BY_MODE[mode]["storage_gb"]),
            }
        # Stash the rationale so the user can inspect why this F-SKU
        # came up (rendered in the data_platform expander or
        # Cost Assumptions sheet).
        prefs["__fabric_estimator_rationale__"] = rationale
        seeded_analytics = True

    if pat["sql_migration"] and _is_empty(prefs.get("azure_sql_db")):
        prefs["azure_sql_db"] = dict(_AZ_SQL_BY_MODE[mode])

    if pat["oltp"] and not pat["sql_migration"] and not pat["pg"] and not pat["mysql"]:
        # Generic OLTP, no engine hint → default to Postgres Flexible
        # (open-source, cheaper at small scale than Azure SQL DB).
        if _is_empty(prefs.get("postgres_flexible")):
            prefs["postgres_flexible"] = dict(_PG_MY_BY_MODE[mode])

    if pat["pg"] and _is_empty(prefs.get("postgres_flexible")):
        prefs["postgres_flexible"] = dict(_PG_MY_BY_MODE[mode])

    if pat["mysql"] and _is_empty(prefs.get("mysql_flexible")):
        prefs["mysql_flexible"] = dict(_PG_MY_BY_MODE[mode])

    if pat["cosmos"] and _is_empty(prefs.get("cosmos_serverless")) and _is_empty(prefs.get("cosmos_ru_per_second")):
        # Mode → Cosmos posture: Saving = serverless (pay-per-RU),
        # Normal/HP = provisioned autoscale with sensible baseline.
        if mode == "saving":
            prefs["cosmos_serverless"] = {"request_units_month": 10_000_000, "storage_gb": 10.0}
        else:
            prefs["cosmos_ru_per_second"] = 4000 if mode == "normal" else 20000

    if pat["cache"] and _is_empty(prefs.get("redis_cache")):
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
    mode: str, prefs: Dict[str, Any], *,
    wants_gpu: bool = False,
    hints: Any = None,
) -> Dict[str, Any]:
    """Seed AI-app baselines per use case + mode.

    `hints`: text-shaped surfaces (signals, suggested_components,
    filename, summary, doc body). Used to detect the use case
    (chatbot internal vs customer-facing, RAG, code, reasoning,
    summarisation, translation, vision, image-gen, speech, embeddings).
    `wants_gpu`: triggers a GPU VM baseline when training / fine-tune
    keywords are present.
    """
    from .pillars.ai_model_picker import recommend_ai_model, detect_ai_use_case

    mode = _resolve_mode(mode)

    # Use-case-aware primary model. Always Azure OpenAI 1st-party
    # unless `prefer_third_party` is set (the Sonnet auto-simulate
    # path can override via suggested_inputs).
    use_case = detect_ai_use_case(*(hints or []))
    primary, fallback = recommend_ai_model(use_case, mode=mode)

    # Seed openai_usage with the picker's primary model.
    if _is_empty(prefs.get("openai_usage")):
        prefs["openai_usage"] = {
            primary.key: dict(_OPENAI_BY_MODE[mode])
        }

    # Stash the rationale + optional 3rd-party fallback so the
    # assumption banner can show why this model was chosen.
    prefs["__ai_model_rationale__"] = (
        f"Use case detected: **{use_case}**. "
        f"Primary model: **{primary.label}** ({primary.provider}). "
        f"{primary.rationale}"
    )
    if fallback is not None:
        prefs["__ai_model_fallback_note__"] = (
            f"Consider Foundry alternative — {fallback.rationale}"
        )

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
    *hints: Any,
) -> Dict[str, Any]:
    """Dispatch to the right pillar helper. Pillars not in the table are
    untouched — they either already produce non-empty BOMs (lift-shift),
    or the per-pillar widgets handle their own defaults (security,
    hybrid_multicloud, m365_and_others).

    `hints`: classifier signals / suggested_components / filename /
    summary — text-shaped surfaces that downstream helpers grep for
    workload-pattern markers. Currently feeds:
      - `detect_gpu_workload()` in the AI pillar (Llama, A100, training)
      - `_detect_data_workload()` in the data pillar (Fabric, Power BI,
        Lakehouse, ETL, OLTP, SQL Server, Cosmos, Redis)
    """
    mode = _resolve_mode(mode)
    if pillar == "ai_application":
        return apply_ai_application_baselines(
            mode, prefs,
            wants_gpu=detect_gpu_workload(*hints),
            hints=hints,
        )
    if pillar == "infra_modernization":
        return apply_infra_modernization_baselines(mode, prefs)
    if pillar == "data_platform":
        return apply_data_platform_baselines(mode, prefs, *hints)
    if pillar == "azure_security":
        return apply_azure_security_baselines(mode, prefs, *hints)
    if pillar == "hybrid_multicloud":
        return apply_hybrid_multicloud_baselines(mode, prefs, *hints)
    return prefs


def apply_hybrid_multicloud_baselines(
    mode: str, prefs: Dict[str, Any], *hints: Any,
) -> Dict[str, Any]:
    """Seed multi-cloud Defender + Arc server counts when AWS / GCP /
    on-prem are mentioned. Mirrors the azure_security baseline but
    targets the hybrid pillar's `multicloud_defender` + `arc_servers`
    fields. Never overwrites existing user picks."""
    mode = _resolve_mode(mode)
    blob = " ".join(_flatten_text(h) for h in hints).lower()

    # Detect non-Azure footprints
    has_aws   = any(k in blob for k in ("aws", "amazon web services", "ec2", "eks", "s3"))
    has_gcp   = any(k in blob for k in ("gcp", "google cloud", "gke", "gcs", "compute engine"))
    has_onprem = any(k in blob for k in ("on-prem", "on prem", "data center", "datacenter",
                                          "vmware", "rvtools", "physical server"))
    if not (has_aws or has_gcp or has_onprem):
        return prefs  # not a multicloud workload — let user populate manually

    # Default Defender plans for multi-cloud — Servers P2 + CSPM are
    # always recommended when you connect non-Azure resources via Arc.
    defender = list(prefs.get("multicloud_defender") or [])
    rationale_parts = []
    for plan in ("servers_p2", "cspm", "storage"):
        if plan not in defender:
            defender.append(plan)
    prefs["multicloud_defender"] = defender

    # Conservative Arc server count defaults — placeholder until the user
    # types a real number.
    arc = dict(prefs.get("arc_servers") or {})
    if has_aws and not arc.get("aws"):
        arc["aws"] = 10
    if has_gcp and not arc.get("gcp"):
        arc["gcp"] = 10
    if has_onprem and not arc.get("onprem"):
        arc["onprem"] = 25
    prefs["arc_servers"] = arc

    rationale_parts.append(
        f"Multi-cloud detected (AWS={has_aws}, GCP={has_gcp}, "
        f"on-prem={has_onprem}). Seeded Defender plans: {defender}. "
        f"Arc server counts: {arc} (placeholders — override in the widget)."
    )
    prefs["__multicloud_rationale__"] = " ".join(rationale_parts)
    return prefs


# --- azure_security: Defender for Cloud baselines per CAF tier + workload --
#
# Microsoft's Defender for Cloud has TWO orthogonal layers:
#   1. CSPM (posture management):
#      - Foundational CSPM    — free, ON by default
#      - Defender CSPM        — paid (~$5/server/mo + ~$0.02/billable resource)
#   2. CWP (Cloud Workload Protection) plans — paid per resource type.
#
# CAF Cloud Adoption Framework guidance
# (/azure/cloud-adoption-framework/secure/) maps each landing-zone tier
# to a recommended Defender baseline:
#
#   Basic LZ       → Foundational CSPM + Defender Servers P2 (for any VM)
#   Foundation LZ  → + Defender Storage, Defender Key Vault
#   Standard LZ    → + Defender CSPM, Defender for Resource Manager + DNS,
#                    Defender SQL on VMs (if SQL present),
#                    Defender App Service (if App Service present),
#                    Sentinel (Pay-as-you-go ingestion)
#   Enterprise LZ  → + Defender Containers (if AKS),
#                    Defender for APIs, Defender Cosmos DB (if Cosmos),
#                    Defender for OSS RDBMS (if PG/MySQL),
#                    Defender for AI workloads (if Azure OpenAI / Foundry)
#
# Per-workload triggers (added on top of the tier baseline regardless of
# LZ preset):
#   sql / mssql in hints      → defender_sql_on_vms
#   aks / kubernetes / k8s    → defender_containers
#   app service / app-service → defender_appsvc
#   cosmos                    → defender_cosmos (if catalog has it)
#   postgres / mysql / maria  → defender_oss_db
#   openai / foundry / gpt    → defender_ai
#   purview / data catalog    → purview component
#   privileged identity / pim → pim component

_LZ_PRESET_SECURITY_BASELINE: Dict[str, List[str]] = {
    "Basic":      ["cspm", "servers_p2"],
    "Foundation": ["cspm", "servers_p2", "storage", "keyvault"],
    # Standard adds plane-level coverage (DNS + Resource Manager) +
    # Sentinel ingestion, per CAF "Secure" methodology.
    "Standard":   ["cspm", "servers_p2", "storage", "keyvault", "sentinel",
                   "dns", "rm"],
    # Enterprise extends with private-link / PIM and pre-arms the
    # advanced Defender plans Microsoft recommends for enterprise
    # landing zones with mixed workloads.
    "Enterprise": ["cspm", "servers_p2", "storage", "keyvault", "sentinel",
                   "dns", "rm", "private_link", "pim"],
}

_SECURITY_WORKLOAD_TRIGGERS: List[Tuple[str, Tuple[str, ...]]] = [
    ("sql_on_vms", ("sql server", "mssql", "sql on vm", "sql-server", "-sql-")),
    ("containers", ("aks", "kubernetes", "k8s", "container apps", "containers")),
    ("appsvc",     ("app service", "app-service", "appsvc", "premium v3",
                    "p1v3", "p2v3", "p3v3", "p1mv3")),
    ("waf",        ("waf", "web application firewall", "app gateway")),
    ("purview",    ("purview", "data catalog", "lineage", "data classification")),
    ("pim",        ("pim ", "privileged identity", "just-in-time", " jit ", "eligible role")),
    # New (Phase-2 follow-up):
    ("cosmos",     ("cosmos", "document db", "documentdb", "cosmos db")),
    ("oss_db",     ("postgres", "postgresql", "mysql", "mariadb", "open-source rdbms")),
    ("ai",         ("openai", "azure openai", "gpt-4", "gpt-4o", "gpt-4.1",
                    "foundry", "llama", "mistral", "cognitive services",
                    "ai search", "rag pipeline", "fine-tun")),
    ("apis",       ("api management", "apim", "azure api", "api gateway",
                    "external api", "public api")),
]


def apply_azure_security_baselines(
    mode: str, prefs: Dict[str, Any], *hints: Any,
) -> Dict[str, Any]:
    """Seed the security pillar's `enabled` list from the LZ preset +
    workload patterns. The tier baseline matches Microsoft's CAF
    "Secure" methodology; workload triggers add specific Defender
    plans when the upload mentions the resource type.

    Reads `prefs.get("lz_preset")` for the tier (the lift-shift pillar
    seeds this even when no LZ checkbox is ticked). Falls through to
    "Foundation" baseline if no preset is set.
    """
    mode = _resolve_mode(mode)
    blob = " ".join(_flatten_text(h) for h in hints).lower()
    # Resolve preset in priority order: explicit prefs key (saved
    # state) → session_state live pick (current render cycle) →
    # Foundation default.
    preset = prefs.get("lz_preset")
    if not preset:
        try:
            import streamlit as _st
            preset = _st.session_state.get("lz_preset")
        except Exception:
            preset = None
    preset = preset or "Foundation"
    enabled: List[str] = list(prefs.get("enabled") or [])

    # Tier baseline — only add components that aren't already in the
    # user's enabled list so manual picks survive.
    tier_baseline = _LZ_PRESET_SECURITY_BASELINE.get(preset, _LZ_PRESET_SECURITY_BASELINE["Foundation"])
    rationale_parts = [f"LZ preset `{preset}` → Defender baseline: {tier_baseline}."]

    for c in tier_baseline:
        if c not in enabled:
            enabled.append(c)

    # Workload triggers on top.
    workload_added: List[str] = []
    for comp_key, tokens in _SECURITY_WORKLOAD_TRIGGERS:
        if comp_key in enabled:
            continue
        if any(t in blob for t in tokens):
            enabled.append(comp_key)
            workload_added.append(comp_key)
    if workload_added:
        rationale_parts.append(
            f"Workload triggers detected → added: {workload_added}."
        )

    prefs["enabled"] = enabled
    prefs["__defender_rationale__"] = " ".join(rationale_parts)
    return prefs
