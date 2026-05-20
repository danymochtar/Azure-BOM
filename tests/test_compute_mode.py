"""Cross-pillar baseline + workload-detection tests.

Locks down the regex-driven detectors that decide which Defender plans
auto-enable, which Fabric F-SKU is recommended, which AI use case maps
to which OpenAI model, and which compute-mode baselines fire per pillar.

These functions have no external dependencies (regex + lookup tables)
so they're fast + deterministic. Most of the recent regressions came
from token-list bit-rot, so cover both positive + negative cases.
"""
from __future__ import annotations

import pytest

from src.compute_mode import (
    apply_baselines,
    detect_gpu_workload,
    recommend_defender_baseline,
    recommend_fabric_capacity,
    _parse_data_size_gb,
    _parse_batches_per_day,
    _parse_table_count,
    _parse_pbi_users,
    _detect_fabric_workloads,
    _detect_data_workload,
)


# ---- GPU detection ---------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Fine-tune Llama 3.3 70B on company data",                  True),
    ("Need an H100 cluster for model training",                  True),
    ("NDmsv4 GPU pool for inference at scale",                   True),
    ("Self-host Stable Diffusion",                               True),
    # Negatives — common false-positive triggers
    ("RAG chatbot using Azure OpenAI",                           False),
    ("No training required — just inference via API",            False),
    ("Web app with App Service",                                 False),
])
def test_gpu_detection(text, expected):
    assert detect_gpu_workload(text) is expected


# ---- Fabric Capacity Estimator --------------------------------------

def test_fabric_estimator_poc_scope():
    """Small POC (10 GB, 1 batch, 3 tables, DF + PBI 10 users) →
    F8 minimum (~5 CU)."""
    cu, sku, rationale = recommend_fabric_capacity(
        data_gb_compressed=10, daily_batches=1, num_tables=3,
        workloads=["data_factory", "power_bi"], pbi_users=10,
    )
    assert sku in ("F4", "F8")
    assert 3 <= cu <= 10


def test_fabric_estimator_production_scope():
    """5 TB / hourly / 250 users / full workloads → F64+ (~50 CU)."""
    cu, sku, _ = recommend_fabric_capacity(
        data_gb_compressed=5120, daily_batches=24, num_tables=50,
        workloads=["data_factory", "spark_jobs", "data_warehouse",
                   "data_science", "power_bi"],
        pbi_users=250,
    )
    assert sku in ("F64", "F128")
    assert cu >= 40


def test_fabric_estimator_floor_is_f2():
    """Minimum F-SKU is F2 regardless of small inputs."""
    cu, sku, _ = recommend_fabric_capacity(
        data_gb_compressed=1, daily_batches=1, num_tables=1, workloads=[],
    )
    assert sku == "F2"


# ---- Text parsers ----------------------------------------------------

@pytest.mark.parametrize("text,expected_gb", [
    ("10GB CSV file",         10.0),
    ("1.5 TB compressed",     1.5 * 1024),
    ("500 MB",                500 / 1024),
    ("2 petabytes archive",   2 * 1024 * 1024),
    ("no size mentioned",     None),
])
def test_parse_data_size_gb(text, expected_gb):
    got = _parse_data_size_gb(text)
    if expected_gb is None:
        assert got is None
    else:
        assert abs(got - expected_gb) < 0.001


@pytest.mark.parametrize("text,expected", [
    ("hourly ETL pipeline",                  24),
    ("daily refresh",                        1),
    ("4 times per day",                      4),
    ("every 15 minutes",                     96),
    ("real-time streaming",                  144),
    ("no schedule mentioned",                None),
])
def test_parse_batches_per_day(text, expected):
    assert _parse_batches_per_day(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("50 tables across sources",         50),
    ("2-3 tables for POC",               3),    # upper bound
    ("100 tables in the warehouse",      100),
    ("no tables mentioned",              None),
])
def test_parse_table_count(text, expected):
    assert _parse_table_count(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("250 BI users",                          250),
    ("concurrent user load of 10",            10),
    ("50 analysts daily",                     50),
    ("serving 1000 customers",                1000),
    ("nothing mentioned",                     None),
])
def test_parse_pbi_users(text, expected):
    assert _parse_pbi_users(text) == expected


# ---- Data workload pattern detection --------------------------------

def test_detect_analytics_pattern():
    pat = _detect_data_workload(
        "Fabric Lakehouse with Power BI dashboards, Data Factory ETL",
    )
    assert pat["analytics"] is True
    assert pat["sql_migration"] is False


def test_detect_sql_migration_pattern():
    pat = _detect_data_workload(
        "Migrate SQL Server with SSIS packages and stored procedures",
    )
    assert pat["sql_migration"] is True


def test_detect_oltp_no_engine_hint():
    pat = _detect_data_workload(
        "Transactional database for e-commerce, high-throughput writes",
    )
    assert pat["oltp"] is True


def test_detect_engine_specific():
    pat = _detect_data_workload("PostgreSQL Flexible Server")
    assert pat["pg"] is True
    pat = _detect_data_workload("MySQL Flexible cluster")
    assert pat["mysql"] is True


# ---- apply_baselines (cross-pillar) ---------------------------------

def test_data_platform_baseline_fabric_path():
    """Analytics-only doc → Fabric seeded, no OLTP databases."""
    prefs = {}
    apply_baselines(
        "data_platform", "normal", prefs,
        "Fabric Lakehouse, Power BI for 50 analysts, Data Factory ETL",
    )
    assert prefs.get("fabric_sku") is not None
    assert "mysql_flexible" not in prefs
    assert "postgres_flexible" not in prefs


def test_data_platform_baseline_oltp_path():
    """Pure OLTP doc → Postgres seeded, no Fabric."""
    prefs = {}
    apply_baselines(
        "data_platform", "normal", prefs,
        "E-commerce shopping cart with PostgreSQL high-throughput writes",
    )
    assert prefs.get("postgres_flexible") is not None
    assert prefs.get("fabric_sku") is None


def test_data_platform_baseline_sparse_falls_to_fabric():
    """No signal at all → assume analytics (most common 2026 default)."""
    prefs = {}
    apply_baselines("data_platform", "normal", prefs, "data platform project")
    assert prefs.get("fabric_sku") is not None


def test_data_platform_baseline_preserves_user_pick():
    """Existing fabric_sku = F128 must NOT be overwritten."""
    prefs = {"fabric_sku": "F128"}
    apply_baselines("data_platform", "saving", prefs, "Fabric project")
    assert prefs["fabric_sku"] == "F128"


# ---- Defender baseline (CAF-mandatory cross-pillar) ----------------

def test_defender_baseline_basic_preset_uses_plan_1():
    """Basic / Foundation CAF tiers route to Defender Servers Plan 1
    (~$5/server/mo) — cost-conscious for dev/test-heavy footprints."""
    plans, _, _ = recommend_defender_baseline(
        "Basic", "Generic VM workload", vm_count=10,
    )
    assert "cspm" in plans
    assert "servers_p1" in plans
    assert "servers_p2" not in plans
    # Basic shouldn't include enterprise-tier additions
    assert "private_link" not in plans


def test_defender_baseline_standard_preset_uses_plan_2():
    """Standard / Enterprise CAF tiers upgrade to Plan 2
    (~$15/server/mo) — production needs FIM + JIT + adaptive controls
    + compliance dashboard."""
    plans, _, _ = recommend_defender_baseline(
        "Standard", "Generic prod workload", vm_count=20,
    )
    assert "servers_p2" in plans
    assert "servers_p1" not in plans


def test_defender_baseline_workload_triggers():
    plans, manual, _ = recommend_defender_baseline(
        "Enterprise",
        "Azure OpenAI RAG + SQL Server cluster + AKS + APIM",
        vm_count=20,
    )
    assert "ai" in plans
    assert "sql_on_vms" in plans
    assert "containers" in plans
    assert "apis" in plans
    # sql_on_vms count scales with vm_count (~25%)
    assert manual["sql_on_vms"] == 5.0


def test_defender_baseline_drops_sentinel_when_pillar_active():
    """When azure_security pillar IS active, baseline yields the
    Sentinel line to the pillar to avoid duplicates."""
    plans, _, _ = recommend_defender_baseline(
        "Standard", "any workload", azure_security_active=True,
    )
    assert "sentinel" not in plans


def test_defender_baseline_keeps_sentinel_when_pillar_inactive():
    plans, _, _ = recommend_defender_baseline(
        "Standard", "any workload", azure_security_active=False,
    )
    assert "sentinel" in plans
