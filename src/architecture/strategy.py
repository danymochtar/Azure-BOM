"""Migration strategy constants + prompt guidance for the AI parser."""

MIGRATION_STRATEGIES = {
    "iaas": {
        "label": "Full Lift-and-Shift (IaaS only)",
        "description": "One-to-one VM mapping. No refactoring. Fastest path.",
    },
    "hybrid": {
        "label": "Hybrid (IaaS + PaaS where it's a clear win)",
        "description": "Keep most as VMs; recommend PaaS for obvious candidates (SQL, web apps).",
    },
    "paas": {
        "label": "Maximize PaaS (refactor/replatform)",
        "description": "Aggressively suggest PaaS alternatives. May require code changes.",
    },
}


def strategy_guidance(strategy: str) -> str:
    """Text appended to the AI parser prompt so Claude adjusts recommendations."""
    if strategy == "iaas":
        return (
            "The target architecture is FULL LIFT-AND-SHIFT. For each server, "
            "recommend only Azure VM SKUs. Set `recommended_azure_service` to "
            "'Azure Virtual Machine'."
        )
    if strategy == "hybrid":
        return (
            "The target architecture is HYBRID IAAS+PAAS. For each server, "
            "suggest a PaaS alternative in `recommended_azure_service` ONLY when "
            "there is a clear, low-risk win (examples: SQL Server -> 'Azure SQL "
            "Managed Instance'; IIS/Nginx web tier -> 'Azure App Service'; Redis "
            "-> 'Azure Cache for Redis'). Otherwise set 'Azure Virtual Machine'."
        )
    if strategy == "paas":
        return (
            "The target architecture is MAXIMIZE PAAS. Aggressively recommend "
            "PaaS alternatives wherever plausible (Azure SQL MI/DB/Hyperscale, "
            "App Service, Container Apps, AKS, Azure Cache for Redis, Azure "
            "Kubernetes Service, Azure Functions, Azure Files, Azure Storage). "
            "Note required refactor effort in `notes`."
        )
    return ""
