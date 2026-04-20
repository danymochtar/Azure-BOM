"""Map a BomLine's resource text to an Azure service name (Pricing Calculator).

Keeps the mapping in one place so every builder can fall back to it when it
doesn't set `service_name` explicitly.
"""
from __future__ import annotations


_KEYWORDS = [
    ("virtual machine", "Virtual Machines"),
    ("managed disk", "Managed Disks"),
    ("azure firewall", "Azure Firewall"),
    ("firewall", "Azure Firewall"),
    ("azure bastion", "Azure Bastion"),
    ("bastion", "Azure Bastion"),
    ("expressroute gateway", "VPN Gateway"),
    ("expressroute", "ExpressRoute"),
    ("vpn gateway", "VPN Gateway"),
    ("application gateway", "Application Gateway"),
    ("load balancer", "Load Balancer"),
    ("public ip", "IP Addresses"),
    ("log analytics", "Azure Monitor"),
    ("sentinel", "Microsoft Sentinel"),
    ("defender cspm", "Microsoft Defender for Cloud"),
    ("defender for", "Microsoft Defender for Cloud"),
    ("key vault", "Key Vault"),
    ("recovery services vault", "Azure Backup"),
    ("azure backup", "Azure Backup"),
    ("backup storage", "Azure Backup"),
    ("azure site recovery", "Azure Site Recovery"),
    ("site recovery", "Azure Site Recovery"),
]


def derive_service_name(category: str, resource: str) -> str:
    text = (resource or "").lower()
    for k, name in _KEYWORDS:
        if k in text:
            return name
    if category.lower() == "compute":
        return "Virtual Machines"
    if category.lower() == "storage":
        return "Managed Disks"
    return category or ""


def derive_custom_name(category: str, resource: str, app_name: str = "") -> str:
    """Sensible default custom name for lines without an explicit one.

    Format: '<app>-<role>' when app_name is provided, else just '<role>'.
    """
    text = (resource or "").lower()
    role = None
    if "virtual machine" in text:
        role = "Compute"
    elif "managed disk" in text:
        role = "Data Disk"
    elif "firewall" in text:
        role = "Hub-Firewall"
    elif "bastion" in text:
        role = "Hub-Bastion"
    elif "vpn" in text and "gateway" in text:
        role = "Hub-VPN"
    elif "expressroute circuit" in text:
        role = "Hub-ER-Circuit"
    elif "expressroute gateway" in text:
        role = "Hub-ER-Gateway"
    elif "application gateway" in text:
        role = "AppGw-WAF"
    elif "load balancer" in text:
        role = "Load-Balancer"
    elif "public ip" in text:
        role = "Public-IP"
    elif "log analytics" in text:
        role = "LogAnalytics"
    elif "sentinel" in text:
        role = "Sentinel"
    elif "defender cspm" in text:
        role = "Defender-CSPM"
    elif "defender" in text:
        role = "Defender"
    elif "key vault" in text:
        role = "KeyVault"
    elif "backup" in text or "recovery services" in text:
        role = "Backup"
    elif "site recovery" in text:
        role = "ASR"
    else:
        role = category or "Workload"
    return f"{app_name}-{role}" if app_name else role
