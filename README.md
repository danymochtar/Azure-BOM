# Azure BOM Generator

Generate an Azure Bill of Materials (BOM) and pricing estimate from an on-prem
inventory (RVTools export, generic Excel/CSV, or infra list). Adds a basic
enterprise-scale landing zone and Microsoft Defender for Cloud plans, then
produces:

- An **Excel BOM** with per-resource SKUs, quantities, and monthly/annual cost
- An **Azure Pricing Calculator import file** (JSON) to load the estimate in the calculator
- Per-resource **deep links** to the Azure Pricing Calculator product pages

Pricing is pulled live from the public Azure Retail Prices API
(https://prices.azure.com/api/retail/prices) — no authentication required.

## Features

- **Migration strategy** (IaaS / Hybrid / PaaS): Claude adjusts its target-service
  recommendations per workload. Full IaaS = VMs only. Hybrid = PaaS where a
  clear win (SQL Server → Azure SQL MI, web tier → App Service). Full PaaS =
  aggressive refactor suggestions.
- **Modular BOM scope**: check the boxes you want priced — Landing Zone, High
  Availability (2× compute + Standard LB), BCDR (Azure Site Recovery + GRS
  backup), and a **security tier** (None / Basic: Defender CSPM + Azure
  Monitor / Full: all Defender workload plans + Log Analytics + Sentinel).
  Uncheck everything and you'll price compute only.
- **AI parser (Claude Opus 4.7)**: handles *any* inventory layout — tabular
  (RVTools vInfo), pivoted/key-value (server-per-section with attributes like
  "RAM (GB)" and "Disk (GB)" down a column), free-text specs
  (`"2 x Xeon Gold 6346 (16 Cores)"`, `"D:4 x 1.9TB SSD, RAID 10"`), and mixed
  units. Small files (≤500 rows) get full direct extraction with RAID
  accounting + vCPU inference from physical CPU specs. Large files use a
  column-mapping spec applied locally.
- **Heuristic fallback**: RVTools `vInfo` sheet or CSV with recognizable
  headers — no AI key required.
- **Sizing**: right-sizes VMs with a configurable headroom factor against a
  curated Azure VM SKU catalog (D-series / E-series / B-series)
- **Disks**: maps provisioned storage to Managed Disks (default Premium SSD)
- **Networking & LZ add-ons**: Hub VNet, Azure Firewall, Bastion, VPN Gateway,
  Log Analytics, Key Vault, Recovery Services Vault (toggleable)
- **Defender for Cloud**: Servers P2, SQL, Storage, App Service, Key Vault,
  Containers (toggleable per plan)
- **Outputs**: Excel BOM + Pricing Calculator import JSON + shareable product
  deep links

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open the URL Streamlit prints (typically http://localhost:8501), upload your
inventory, pick a region, adjust the landing-zone and Defender toggles, and
download the BOM.

## Input formats

### AI parser (recommended — any format)

Toggle **"Use AI parser (Claude Opus 4.7)"** in the sidebar and paste your
Anthropic API key. Claude inspects every sheet, picks the one that looks like a
VM inventory, and maps its columns to our normalized schema — including unit
inference (MiB vs GB, MB vs GB) and powered-off filtering. The file is
processed locally after the mapping arrives; only a header + row sample is sent
to the API.

Set the key via `.streamlit/secrets.toml` on Streamlit Cloud:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
```

### RVTools

Upload the RVTools `.xlsx` directly. The tool reads the `vInfo` sheet and pulls
`VM`, `CPUs`, `Memory`, `Provisioned MiB` (or `Provisioned MB`), `OS` / `OS
according to the configuration file`, and `Powerstate`. Powered-off VMs are
excluded by default.

### Generic inventory

Excel or CSV with columns (case-insensitive, flexible names accepted):

| name | vcpu | memory_gb | storage_gb | os | environment |
|------|------|-----------|------------|----|--|
| app01 | 4 | 16 | 200 | Windows | prod |

Aliases recognized: `hostname`/`server`/`vm` for name; `cpu`/`cpus`/`cores` for
vcpu; `ram`/`ram_gb`/`memory`/`mem_gb` for memory; `disk`/`disk_gb`/`storage`
for storage.

## Output

- `azure-bom.xlsx` — tabs: `Summary`, `Compute`, `Storage`, `Networking`,
  `Security`, `Management`, `All Line Items`
- `pricing-calculator-import.json` — paste into the Azure Pricing Calculator
  via its "Import" feature
- Clickable Pricing Calculator links rendered in the UI per resource category

## Notes

- Retail pricing ≠ your negotiated EA/MCA/CSP pricing; apply your discount in
  the exported Excel if needed.
- The VM SKU catalog covers common general-purpose and memory-optimized sizes.
  Edit `src/mapper/vm_catalog.py` to add more.
- Right-sizing is a heuristic; always validate against workload benchmarks
  before committing to a design.
