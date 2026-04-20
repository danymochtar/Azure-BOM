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

- **Inputs**: RVTools `vInfo` sheet, generic inventory Excel/CSV (auto-mapped
  columns), or hand-built infra list
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
