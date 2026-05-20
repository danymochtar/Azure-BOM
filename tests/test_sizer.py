"""VM sizer + workload-token heuristic contract tests.

Covers the right-sizing policy (1:1 exact match, nearest cost-optimised
fit, mode-aware family preference) and the name-token detectors that
drive env/role grouping, SQL Server licensing, and Burstable routing.
"""
from __future__ import annotations

import pytest

from src.models import InventoryItem
from src.mapper import (
    recommend_vm, recommend_disk, recommend_disk_tier,
    is_non_prod, is_sql_server, env_tag, role_tag,
)


def _vm(name: str, vcpu: int = 4, mem: float = 16, env: str = ""):
    return InventoryItem(
        name=name, vcpu=vcpu, memory_gb=mem, storage_gb=100, environment=env,
    )


# ---- recommend_vm — sizing policy ------------------------------------

def test_exact_match_picks_dsv5():
    """4 vCPU / 16 GB → D4s_v5, no padding."""
    sku = recommend_vm(_vm("server-1", 4, 16))
    assert sku.arm_name == "Standard_D4s_v5"


def test_memory_heavy_routes_to_esv5():
    """Memory-to-vCPU ratio ≥ 6 → E-series."""
    sku = recommend_vm(_vm("db-server", 4, 32))   # ratio 8
    assert sku.family == "memory"
    assert sku.arm_name == "Standard_E4s_v5"


def test_headroom_skips_exact_match():
    """When user explicitly asks for safety margin, don't take the
    1:1 fast path."""
    sku = recommend_vm(_vm("padded-vm", 4, 16), headroom=1.3)
    # 5.2 vCPU / 20.8 GB required → smallest fit is D8s_v5
    assert sku.vcpu >= 5


# ---- compute_mode integration ----------------------------------------

def test_saving_mode_routes_non_prod_to_burstable():
    """UAT VM at 4/16 → B4ms in saving mode."""
    sku = recommend_vm(_vm("erp-uat-app", env="uat"), compute_mode="saving")
    assert sku.family == "burstable"
    assert sku.arm_name == "Standard_B4ms"


def test_saving_mode_keeps_prod_on_dsv5():
    """Saving mode doesn't silently downgrade prod to Burstable."""
    sku = recommend_vm(_vm("erp-prod-app", env="prod"), compute_mode="saving")
    assert sku.family == "general"
    assert sku.arm_name == "Standard_D4s_v5"


def test_high_perf_avoids_burstable_for_small_vms():
    """HP mode explicitly de-prioritises Burstable — small VMs that
    would normally route there still go to D-series."""
    sku = recommend_vm(_vm("small-app", 2, 8), compute_mode="high_perf")
    assert sku.family != "burstable"


def test_high_perf_memory_heavy_still_e_series():
    sku = recommend_vm(_vm("big-db", 8, 64), compute_mode="high_perf")
    assert sku.family == "memory"


# ---- env_tag --------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Server 1 — PROD Application Server",   "prod"),
    ("Server 3 — UAT Application Server",    "uat"),
    ("erp-dev-app1",                         "dev"),
    ("staging-web-1",                        "preprod"),   # 'stage' → preprod
    ("preprod-app",                          "preprod"),
    ("abc-sit-env",                          "sit"),       # "-sit-" substring
    ("env-qa-tools",                         "qa"),        # "-qa-" substring
    ("untagged-server",                      "prod"),      # default
])
def test_env_tag_detection(name, expected):
    assert env_tag(_vm(name)) == expected


def test_env_priority_order_test_beats_sandbox():
    """Token order: 'test' is checked before 'sandbox' in _ENV_PRIORITY,
    so a name containing both routes to 'test'. Documented behaviour."""
    assert env_tag(_vm("sandbox-test-server")) == "test"


# ---- role_tag -------------------------------------------------------

@pytest.mark.parametrize("name,expected_role", [
    ("Server 2 — DB (PROD)",                          "db"),
    ("mssql-prod-cluster-01",                         "sql"),
    ("Server 4 — Jasper Report Server for UAT & PROD","report"),
    ("erp-app-server",                                "app"),       # "-app-" token
    ("web-frontend-1",                                "web"),
    ("jumphost",                                      "jumphost"),
    ("backup-vault-01",                               "backup"),
    ("syslog-collector-1",                            "log"),
    ("untagged-server",                               "generic"),
])
def test_role_tag_detection(name, expected_role):
    assert role_tag(_vm(name)) == expected_role


def test_role_iis_classified_as_web():
    """`iis` is in the `web` token list and `web` is checked before
    `app` in _ROLE_TOKENS — so an IIS-on-app-server name routes to
    'web'. Documented behaviour."""
    assert role_tag(_vm("app-server-iis-01")) == "web"


# ---- is_sql_server --------------------------------------------------

def test_is_sql_server_specific_tokens():
    assert is_sql_server(_vm("mssql-prod-01")) is True
    assert is_sql_server(_vm("sql-server-uat-02")) is True
    assert is_sql_server(_vm("erp-sql-db-prod")) is True


def test_is_sql_server_generic_db_does_not_trigger():
    """A generic 'db' name (could be Oracle/Postgres/Mongo) should
    NOT auto-add a SQL Server license line."""
    assert is_sql_server(_vm("Server 2 — DB (PROD)")) is False
    assert is_sql_server(_vm("oracle-prod-1")) is False
    assert is_sql_server(_vm("postgres-shard-3")) is False


# ---- is_non_prod ----------------------------------------------------

def test_is_non_prod_from_env_field():
    assert is_non_prod(_vm("anything", env="uat")) is True
    assert is_non_prod(_vm("anything", env="prod")) is False


def test_is_non_prod_from_name():
    assert is_non_prod(_vm("erp-staging-app")) is True
    assert is_non_prod(_vm("Server 3 — UAT Application Server")) is True


def test_is_non_prod_default_is_false():
    """Untagged VM defaults to prod (conservative — don't accidentally
    throttle real prod workloads to Burstable)."""
    assert is_non_prod(_vm("untagged-vm")) is False


# ---- recommend_disk_tier -------------------------------------------

def test_disk_tier_db_routes_to_premium():
    assert recommend_disk_tier(_vm("erp-prod-sql-01")) == "Premium SSD"
    assert recommend_disk_tier(_vm("mongo-shard")) == "Premium SSD"


def test_disk_tier_backup_routes_to_hdd():
    assert recommend_disk_tier(_vm("backup-vault-01")) == "Standard HDD"
    assert recommend_disk_tier(_vm("syslog-collector")) == "Standard HDD"


def test_disk_tier_default_standard_ssd():
    assert recommend_disk_tier(_vm("app-server")) == "Standard SSD"


# ---- recommend_disk size ladder ------------------------------------

def test_disk_size_ladder_finds_smallest_fit():
    """A 200 GB requirement should land on P15 (256 GiB) not P10
    (128 GiB)."""
    disk = recommend_disk(200, tier="Premium SSD")
    assert disk.size_gib >= 200
    # The next rung below would be insufficient
    assert disk.sku in ("P15", "P20")


def test_disk_size_ladder_tier_prefix():
    p = recommend_disk(100, tier="Premium SSD")
    e = recommend_disk(100, tier="Standard SSD")
    s = recommend_disk(100, tier="Standard HDD")
    assert p.sku.startswith("P")
    assert e.sku.startswith("E")
    assert s.sku.startswith("S")


# ---- Multi-disk per VM ---------------------------------------------

def test_inventory_item_default_disks_empty():
    """Backward compat: VMs without explicit disks list get empty list,
    not None — keeps the legacy single-disk path working."""
    from src.models import InventoryItem
    v = InventoryItem(name="legacy", vcpu=2, memory_gb=8, storage_gb=100)
    assert v.disks == []


def test_inventory_item_with_disks():
    from src.models import InventoryItem, DiskItem
    v = InventoryItem(
        name="db-prod", vcpu=8, memory_gb=64, storage_gb=2048,
        disks=[
            DiskItem(label="OS Disk", size_gb=128),
            DiskItem(label="Data Disk 1", size_gb=1024, tier="Premium SSD"),
            DiskItem(label="Log Disk", size_gb=896, tier="Standard SSD"),
        ],
    )
    assert len(v.disks) == 3
    assert v.disks[1].tier == "Premium SSD"
    # storage_gb should equal sum of disks (validation is at parser
    # level, but the model permits the user to provide both)
    assert v.storage_gb == sum(d.size_gb for d in v.disks)
