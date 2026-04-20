# Azure Cost Assessment

Upload anything — VM inventory, SIEM design doc, AI use-case, data-platform
spec, or a mixed architecture — and get a live-priced Azure estimate matching
Microsoft's Azure Pricing Calculator export template.

**Accepted file types:** Excel (.xlsx / .xls), CSV, PDF, images
(.png / .jpg / .gif / .webp), Word (.docx), plain text / markdown
(.txt / .md / .json / .yml / .yaml / .log).

PDFs and images are read natively by Claude (no OCR preprocessing). DOCX
content — including embedded tables — is text-extracted via python-docx.

## What makes it different

**Classify first, then price.** A cheap Haiku pass classifies the workload
(vm_inventory / siem_soc / ai_ml / data_platform / app_modernization / mixed)
before any expensive extraction runs. The expensive Opus pass only runs when
the file is actually a VM inventory. For a SIEM use-case doc the app skips VM
extraction entirely and goes straight to the Sentinel calculator — saving
~95% on tokens vs a one-size-fits-all prompt.

## Supported workload types

| Workload | Input | Line items produced |
|---|---|---|
| VM Inventory | RVTools, server list, infra spec, pivoted/key-value layouts, free-text specs | VM right-sizing, managed disks, optional LZ/HA/BCDR, Defender, Sentinel |
| App Modernization | Same as VM, with Hybrid/PaaS strategy | Claude recommends Azure SQL DB / App Service / Cache per workload |
| SIEM / SOC | Design doc / ingestion profile | Log Analytics (PAYG or commitment tier), Sentinel, retention, Defender CSPM |
| AI / ML | Design doc / token volume | Azure OpenAI per-model input/output tokens |
| Data Platform | Design doc / capacity needs | Microsoft Fabric capacity, Cosmos DB RU/s, Synapse DWU |
| Mixed | Architecture doc with several | All relevant calculators combined |

## Architecture knobs

- **Primary + Secondary region** (secondary only appears when HA or BCDR is on)
- **Migration strategy**: Full IaaS / Hybrid (Azure SQL DB-first, MI only when needed) / Full PaaS
- **Landing zone** with per-component checkboxes: Public IP, ExpressRoute (circuit + gateway), Azure Firewall, Bastion, VPN Gateway, App Gateway WAF v2, Log Analytics, Key Vault, Azure Backup
- **HA**: 2× compute + Standard Load Balancer; PaaS recommendations become zone-redundant (SQL BC with Always On, App Service ZR)
- **BCDR**: Azure Site Recovery (priced in secondary region) + GRS backup storage
- **Security** (per-component checkboxes): Defender CSPM, Defender for Servers P2 + SQL/Storage/App Service/Key Vault/Containers, Microsoft Sentinel
- **Dynamic sizing**: Azure Backup as % of total disk; Log Analytics as MB/day/VM × VM count × 30

## Pricing data

All prices come live from the public Azure Retail Prices API
(`https://prices.azure.com/api/retail/prices`) — no authentication needed.
Retail ≠ your negotiated EA/MCA/CSP pricing; apply your discount in the
exported Excel.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Streamlit Cloud

1. Push the branch. Go to https://share.streamlit.io.
2. New app → pick this repo → Main file: `app.py` → Deploy.
3. In **Settings → Secrets**:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
```

## Output

- **Excel** with a single `Estimate` sheet matching Microsoft's Azure Pricing
  Calculator Export template — columns: Service category, Service name, Type,
  Custom name, Region, Description, Estimated upfront cost, Estimated monthly
  cost. The workload name you provide prefixes every Custom name.
- **Pricing Calculator import JSON** with `productId` / `skuId` / `meterId` per
  line, for programmatic reproduction.
- **Deep links** to the per-product pages in the live Azure Pricing Calculator.

## Security & data handling

- API keys are held in Streamlit session memory only. Never written to disk.
- When AI features are enabled, a preview of the uploaded file (headers +
  sample rows, or full content for files ≤500 rows) is sent to Anthropic.
  Remove sensitive data (passwords, PII, production hostnames) before upload.
  Anthropic does not train on API inputs per their terms.
- All structured outputs are Pydantic-validated before use.
- The heuristic `row_filter` field on the mapping parser deliberately does NOT
  evaluate pandas queries — `pd.DataFrame.query()` is eval-based and is not
  safe against a prompt-injected preview.
- Azure Retail Prices API is public and requires no data from the user.
