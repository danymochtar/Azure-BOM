# Changelog

Curated highlights from the recent assessment-platform refactor. For
the full commit history, run `git log --oneline`.

## Unreleased — pricing + UX polish

- **Audit-driven cleanup** (this release):
  - Hide "Pre-assessment templates" from sidebar Advanced (unused noise)
  - Group assumption banner by pillar (collapsible per-pillar sub-expanders)
  - `docs/ARCHITECTURE.md` + session-state key registry
  - `CHANGELOG.md` (this file)
- **Chatbot frontend auto-seeded** for AI use cases (App Service P0v3
  normal / B1 saving / P1v3 HP) — Copilot Studio alternative flagged
- **Defender for Cloud → always-on baseline** (CAF-mandatory in every
  assessment, regardless of pillars active); `azure_security` pillar
  narrowed to Sentinel + advanced standalone
- **Multi-cloud Defender baseline** auto-fires when AWS/GCP/on-prem
  hints detected (Arc server counts + servers_p2 + cspm + storage)

## Pricing engine improvements

- **App Gateway WAF v2 base picker fixed** — was grabbing the CU
  meter ($0.0144/hr) instead of the base ($0.443/hr), underbilling
  ~$320/mo per gateway
- **Pickers skip $0 free-tier meters** across LZ / data_platform /
  Defender layers
- **VPN Gateway SKU ladder** (Basic → VpnGw1AZ / 2AZ / 3AZ → VpnGw5AZ)
  with preset-driven defaults; mandatory in all 4 LZ presets
- **Per-VM disk tier auto-routing** — DB → Premium SSD,
  backup/archive → Standard HDD, else Standard SSD (MS 2025 baseline)
- **SQL Server VM license line** auto-added when SQL Server detected
  and AHB-SQL is off (Standard PAYG $0.408/core-hr)
- **Cost-compare safety net** — Burstable vs equivalent D-series,
  pick cheaper per region
- **Mixed billing term** — non-prod VMs stay on PAYG even when
  global is RI/SP (toggle in Assessment context)
- **Region label friendly** in Results table ("Malaysia West (Kuala
  Lumpur)" not "malaysiawest")
- **Billing term column** in Results table
- **Currency locked to USD** (Retail Prices API is USD-native)

## Mode-driven baselines (Saving / Normal / High-Performance)

- **Global compute-mode selector** in Assessment context (default:
  Normal). Biases SKU/tier selection across every pillar
- **Per-pillar mode override** — different mode per pillar (e.g.
  Saving lift-shift + HP data platform)
- **Cross-pillar baselines** for `infra_modernization`,
  `data_platform`, `ai_application` so non-empty BOM appears even
  when auto-simulate is off
- **Fabric Capacity Estimator port** — recommends F-SKU from data
  size + batch frequency + workloads + PBI users, matching MS's
  official estimator
- **AI use-case → model picker** with 12 use cases mapped to
  Azure OpenAI 1st-party (always preferred); Foundry 3rd-party
  alternatives only when there's a documented reason (open-weight,
  cost-at-scale, capability gap)
- **GPU heuristic detection** from upload signals (A100, H100, ND,
  Llama, fine-tune, training) auto-seeds ND/NC SKUs

## UX flow restructure

- **8-step numbered flow**: Upload → Submit → Assessment context →
  Classification → Pillar inputs → Generate → Results → Token spend
- **Submit gate** — no Anthropic API calls until user clicks Submit
  (no token burn on paste / upload)
- **Auto-generate first BOM** after auto-simulate completes (skip
  the "discover the button" moment)
- **Big assumption warning** at top of Results when BOM is shaped
  by auto-simulated values (lists every assumption point)
- **Top-of-Results download CTAs** (Excel + Pricing Calculator JSON)
- **Per-pillar subtotals** in expander headers
- **Combined upload view** — file uploader + paste text + paste
  image (via streamlit-paste-button) on one screen, single Submit
- **Auto-clear cache on refresh** — preserves prefs + API key but
  drops stale BOM + sim cache

## Azure-side improvements

- **Geo-grouped region selectbox** with format_func (🌏 APAC /
  🌎 Americas / 🇪🇺 Europe / 🌍 MEA)
- **Auto-default secondary region** to Azure paired region when
  BCDR/HA enabled (49/49 regions covered)
- **Static reference rates** for every LZ component (dated
  2026-04) — fallback when retail meter missing
- **`malaysiacentral` removed** (it was a guess; Microsoft only
  ships Malaysia West)
- **VM grouping** respects env + role tokens (prod/uat/dev × db/app/
  web/report/etl) — different-named VMs no longer silently merge
- **Resource count** moved to its own `#` column (no more "x4"
  baked into resource name)

## LLM / API hardening

- **Haiku → Sonnet → Opus cascade** on `OverloadedError 529` for
  every Claude call (classifier + auto_simulate + ai_inventory)
- **30K-token/min Tier-1 throttle handling** — 60s wait + retry
  inside auto_simulate; 2s pre-throttle between back-to-back calls
- **Spreadsheet preview capped at 20 rows** for auto_simulate
  (full extraction still happens via ai_inventory.ai_extract_direct)
- **Text content truncated to 200K chars** with head/tail marker
- **Prompt caching** on the system prompt (ephemeral) so the
  cascade doesn't re-bill the schema preamble
