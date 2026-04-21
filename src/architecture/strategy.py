"""Migration strategy constants + per-pillar prompt guidance for the AI parser.

`strategy_guidance(strategy, ha_enabled)` is scoped to the infra_lift_shift
pillar. `pillar_extraction_hint(pillar, strategy, ha_enabled)` dispatches to
the right hint for whichever pillar is running Sonnet extraction.
"""

MIGRATION_STRATEGIES = {
    "iaas": {
        "label": "Full Lift-and-Shift (IaaS only)",
        "description": "One-to-one VM mapping. No refactoring. Fastest path.",
    },
    "hybrid": {
        "label": "Hybrid (IaaS + PaaS where it's a clear win)",
        "description": "Keep most as VMs; recommend Azure SQL DB / App Service / Cache for Redis where the app supports it. MI only when app stays on a VM and the DB uses features Azure SQL DB doesn't cover.",
    },
    "paas": {
        "label": "Maximize PaaS (refactor/replatform)",
        "description": "Aggressively suggest PaaS alternatives. May require code changes.",
    },
}


def strategy_guidance(strategy: str, ha_enabled: bool = False) -> str:
    """Text appended to the AI parser prompt so Claude adjusts recommendations.

    When `ha_enabled` is True, PaaS recommendations steer toward zone-redundant
    tiers (e.g. Azure SQL DB Business Critical with zone redundancy, which
    provides SQL Always On replicas built-in; App Service zone-redundant plan).
    """
    ha_sql_tier = (
        "Azure SQL Database (Business Critical, zone-redundant — SQL Always On "
        "replicas included)" if ha_enabled else "Azure SQL Database (General Purpose)"
    )
    ha_app_tier = (
        "Azure App Service (Premium v3, zone-redundant)"
        if ha_enabled
        else "Azure App Service"
    )

    if strategy == "iaas":
        return (
            "The target architecture is FULL LIFT-AND-SHIFT. For EVERY server, "
            "set `recommended_azure_service` to 'Azure Virtual Machine'. Do not "
            "suggest PaaS alternatives."
        )

    if strategy == "hybrid":
        return (
            "The target architecture is HYBRID IAAS+PAAS. Apply these rules to "
            "each server:\n\n"
            "1. SQL Server workloads:\n"
            f"   - DEFAULT: {ha_sql_tier}. This is the first choice.\n"
            "   - Only recommend 'Azure SQL Managed Instance' when BOTH:\n"
            "     (a) the existing DB uses features Azure SQL DB does NOT "
            "support (cross-database queries, SQL Agent jobs, Service Broker, "
            "CLR, linked servers, filestream, msdb-level features), AND\n"
            "     (b) the application code stays on a VM alongside it.\n"
            "   - Call out the assumed migration path in `notes`.\n"
            "2. Web / app tier (IIS, Nginx, Apache, Tomcat, Node):\n"
            f"   - {ha_app_tier} when the app is containerizable or stateless.\n"
            "   - Otherwise keep as 'Azure Virtual Machine'.\n"
            "3. Caching (Redis / Memcached): 'Azure Cache for Redis'.\n"
            "4. Active Directory, file servers, legacy ERP backends, anything "
            "tightly coupled to OS-level features: keep as 'Azure Virtual "
            "Machine'.\n"
            "5. Everything else: 'Azure Virtual Machine'."
        )

    if strategy == "paas":
        return (
            "The target architecture is MAXIMIZE PAAS. Apply these rules:\n\n"
            f"1. SQL Server: default {ha_sql_tier}. Only use 'Azure SQL "
            "Managed Instance' as a last resort for hard-to-migrate schemas, "
            "and even then plan refactor to Azure SQL DB.\n"
            "2. PostgreSQL / MySQL: 'Azure Database for PostgreSQL Flexible "
            "Server' / 'Azure Database for MySQL Flexible Server'.\n"
            f"3. Web / app tier: prefer {ha_app_tier}, Azure Container Apps, "
            "or AKS if microservices.\n"
            "4. Batch / scheduler jobs: Azure Functions / Container Apps Jobs.\n"
            "5. File servers: 'Azure Files' (Premium).\n"
            "6. Caching: 'Azure Cache for Redis'.\n"
            "7. Queues/messaging: 'Azure Service Bus'.\n"
            "8. For every PaaS recommendation, note the refactor effort "
            "('none', 'config-only', 'schema changes', 'code refactor') in "
            "`notes`. If a workload cannot be PaaS-migrated at all, keep as "
            "'Azure Virtual Machine' and explain why in `notes`."
        )

    return ""


# ---------------------------------------------------------------------------
# Pillar-aware extraction hints
# ---------------------------------------------------------------------------

def pillar_extraction_hint(
    pillar: str,
    strategy: str | None = None,
    ha_enabled: bool = False,
) -> str:
    """Return the text guidance appended to the Sonnet extractor prompt for
    a specific pillar. Currently only infra_lift_shift uses the Sonnet
    extractor; other pillars rely on user-provided inputs, but the hint
    can still shape auto-simulate output for those pillars.
    """
    if pillar == "infra_lift_shift":
        return strategy_guidance(strategy or "iaas", ha_enabled=ha_enabled)

    if pillar == "data_platform":
        return (
            "Recommend Microsoft Fabric as the DEFAULT data-platform "
            "substrate. Fabric F-SKUs cover Power BI, Data Factory, "
            "OneLake, Warehouse, Eventhouse in one capacity. Use F-SKU "
            "sizing by Power BI user count (F2 < 25 users, F4 25-50, "
            "F8-F16 50-200, F32 200-500, F64 500-2k, F128+ beyond). "
            "Azure Databricks should ONLY be recommended when the doc "
            "explicitly calls for (a) existing Databricks estate, (b) "
            "dedicated Spark / Photon compute, (c) Python/ML libraries "
            "not compatible with Fabric notebooks. Azure SQL DB / SQL "
            "MI / PostgreSQL Flexible / MySQL Flexible are recommended "
            "for OLTP workloads distinct from analytics."
        )

    return ""
