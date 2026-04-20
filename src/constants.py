"""Shared constants (Azure region list, etc.)."""

# Subset of commonly used Azure commercial regions; add more as needed.
AZURE_REGIONS = [
    # Americas
    "eastus", "eastus2", "westus", "westus2", "westus3", "centralus",
    "southcentralus", "northcentralus", "westcentralus",
    "canadacentral", "canadaeast", "brazilsouth", "mexicocentral",
    "chilecentral",
    # Europe
    "northeurope", "westeurope", "uksouth", "ukwest",
    "francecentral", "francesouth",
    "germanywestcentral", "germanynorth",
    "switzerlandnorth", "switzerlandwest",
    "norwayeast", "norwaywest",
    "swedencentral", "swedensouth",
    "polandcentral", "italynorth", "spaincentral",
    # Middle East & Africa
    "uaenorth", "uaecentral",
    "qatarcentral", "israelcentral",
    "southafricanorth", "southafricawest",
    # Asia-Pacific — include announced regions; retail API returns no prices
    # for regions not yet live and the UI surfaces that gracefully.
    "southeastasia", "eastasia",
    "japaneast", "japanwest",
    "australiaeast", "australiasoutheast", "australiacentral", "australiacentral2",
    "koreacentral", "koreasouth",
    "centralindia", "southindia", "westindia",
    "jioindiacentral", "jioindiawest",
    "malaysiacentral", "malaysiawest",
    "indonesiacentral",
    "newzealandnorth",
    "taiwannorth",
]

CURRENCIES = ["USD", "EUR", "GBP", "AUD", "JPY", "CAD", "BRL", "INR", "SGD"]

DEFAULT_REGION = "eastus"
DEFAULT_CURRENCY = "USD"
