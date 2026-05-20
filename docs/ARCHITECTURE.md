# Architecture notes — Azure Cost Assessment

Quick map of how the codebase fits together so you can find your way
around without reading every file. Pair this with the inline comments
in each module which spell out the MS Learn references for every
policy decision.

## High-level flow

```
┌─────────────────────────────────────────────────────────────────┐
│  app.py  (Streamlit orchestrator, ~1300 lines)                  │
│  ─────────────────────────────────────────────                  │
│   1. Sidebar — Workload (name, region) + Tools + Advanced       │
│   2. Upload step — file_uploader + paste text + paste image     │
│      → Submit gate (no token burn until clicked)                │
│   3. Assessment context — compute mode, billing term, AHB,      │
│      Defender baseline toggle                                   │
│   4. Workload classification (Haiku → Sonnet → Opus cascade)    │
│   5. Pillar inputs loop:                                        │
│      - auto_simulate (Sonnet)                                   │
│      - apply_baselines (compute_mode.py)                        │
│      - render_inputs (per pillar)                               │
│   6. Generate → fan out to pillar.build_bom + cross-pillar      │
│      Defender baseline + always-on lines                        │
│   7. Results — assumption banner + metrics + downloads + table  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  src/pillars/  (one module per Azure pillar)                    │
│  ─────────────────────────────────────────                      │
│   infra_lift_shift     — VM right-sizing, LZ, HA, BCDR          │
│   infra_modernization  — App Service, AKS, ACR, DevTools        │
│   data_platform        — Fabric, SQL, Cosmos, PG/MySQL, ADLS    │
│   ai_application       — OpenAI tokens, AI Search, GPU, frontend│
│   azure_security       — Sentinel + advanced (Defender is global)│
│   hybrid_multicloud    — Arc, multi-cloud Defender              │
│   m365_and_others      — M365 Backup, Copilot Studio, etc.      │
│                                                                  │
│   Each pillar exposes:                                          │
│     render_inputs(st, prefs, app_name, region, ...) -> dict     │
│     build_bom(client, region, inputs, app_name, ...) -> BomLine │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  src/pricing/  (retail + static fallbacks)                      │
│  ──────────────────────────────────────                         │
│   retail.py        — RetailPricesClient (Azure Retail Prices API)│
│   lz_static.py     — Static rates per LZ component (2026-04 ref)│
│   bandwidth.py     — Tiered egress curve                        │
│   devtools_static.py — GitHub / VS / Azure DevOps per-seat      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  src/compute_mode.py  (cross-pillar policy + baselines)         │
│  ──────────────────────────────────────────────                 │
│   COMPUTE_MODES        — Saving / Normal / High-Performance     │
│   apply_baselines      — dispatcher for non-empty BOM seeding   │
│   recommend_defender_baseline — CAF-mandatory Defender plans    │
│   recommend_fabric_capacity   — port of MS Fabric Estimator     │
│   detect_gpu_workload  — GPU heuristic from text hints          │
│   _detect_data_workload — Fabric vs OLTP vs SQL migration       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  src/mapper/sizer.py  + analysis/builder.py                     │
│  ──────────────────────────────────                             │
│   recommend_vm         — D / E / B / GPU family routing         │
│   recommend_disk       — P / E / S disk SKU per size            │
│   recommend_disk_tier  — workload-driven Premium/Standard/HDD   │
│   is_non_prod / is_sql_server / env_tag / role_tag — name parse │
│   build_compute_bom    — VM + disk + SQL license emission       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  src/models.py — BomLine (the universal output row)             │
│   src/output/ — Excel exporter, Pricing Calculator JSON         │
│   src/llm.py  — Haiku→Sonnet→Opus cascade helper                │
└─────────────────────────────────────────────────────────────────┘
```

## Adding a new pillar

1. **Create the module** `src/pillars/<name>.py` with:

   ```python
   PILLAR_METADATA = {
       "key": "your_pillar",
       "label": "Your Pillar",
       "description": "What it covers in one sentence.",
       "icon": "🆕",
       "needs_vm_extraction": False,
   }

   def render_inputs(st, prefs, app_name, region, upload_bytes,
                    upload_name, profile) -> dict:
       # Streamlit widgets → returns inputs dict for build_bom
       ...

   def build_bom(client, region, inputs, app_name, pricing_mode):
       # Returns (List[BomLine], List[dict] mapping_rows)
       ...
   ```

2. **Register in `src/pillars/__init__.py`**: add to `PILLAR_ORDER`
   + import. The dispatcher does the rest.

3. **Add a Sonnet schema** in `src/pillars/auto_simulate.py`:
   `PILLAR_SCHEMAS["your_pillar"] = "..."`. Sonnet uses this when
   the classifier routes to your pillar.

4. **(Optional) Add a baseline** in `src/compute_mode.py`:

   ```python
   def apply_your_pillar_baselines(mode, prefs, *hints):
       # Seed mode-appropriate defaults for empty fields
       return prefs
   ```

   And wire into `apply_baselines` dispatcher.

5. **(Optional) Add a classifier hint** in
   `src/parsers/classifier.py` `SYSTEM_PROMPT` so Haiku knows when
   to route to your pillar.

## Session-state keys

Streamlit `st.session_state` carries a lot of cross-step state.
Keys are namespaced by convention:

| Prefix / key | Purpose |
|---|---|
| `_submitted_uploads` | Files committed via the Submit button |
| `_assumption_log` | dict[pillar_key, list[assumption_string]] |
| `_pillar_totals` | dict[pillar_key, monthly_usd] |
| `_sim::<hash>::<pillar>::...` | Cached auto-simulate result |
| `_auto_gen::<hash>::<pillars>` | Auto-generate guard (1× per upload combo) |
| `_cm_override::<pillar>` | Per-pillar compute-mode override |
| `_session_fresh_init` | Set once per browser session — refresh clears |
| `_last_sim_call_ts` | Throttle for back-to-back auto-simulate |
| `_anthropic_key` | API key for the current session |
| `_authed` | Login session flag |
| `bom_lines` | List[BomLine] from current Generate |
| `mapping_rows` | per-VM source → target rows from lift-shift |
| `region` / `currency` / `app_name` | Persisted across reruns |
| `compute_mode` | Global compute mode (set by Assessment ctx) |
| `lz_preset` | CAF preset (set by lift-shift widget) |
| `pasted_text_input` / `paste_img_btn` / `ls_*` | Streamlit widget keys |

LocalStorage (`src/storage.py`) persists a subset across sessions:

| LocalStorage key | Restored on |
|---|---|
| `prefs` | App load (sidebar + Assessment ctx pre-fill) |
| `anthropic_api_key` | App load (if Remember key is on) |
| `last_bom` | Cleared on refresh (see `_session_fresh_init`) |
| `auth_ok` | App load (skip login) |

## Cross-pillar wiring (`__double_underscore__` keys)

Injected into every pillar's `inputs` dict by `app.py` so pillars
don't have to know about each other:

| Key | Set by | Read by |
|---|---|---|
| `__use_ahb__` | app.py sidebar | infra_lift_shift, ai_application (GPU VMs) |
| `__use_ahb_sql__` | app.py sidebar | data_platform, analysis/builder |
| `__non_prod_payg__` | app.py Assessment ctx | analysis/builder VM grouping |
| `__compute_mode__` | app.py + per-pillar override | every pillar that picks SKU/tier |
| `__exports__` | infra_lift_shift output | azure_security (vm_count + la_gb) |
| `__fabric_estimator_rationale__` | compute_mode baseline | assumption log |
| `__ai_model_rationale__` | compute_mode baseline | assumption log |
| `__ai_model_fallback_note__` | compute_mode baseline | assumption log |
| `__frontend_rationale__` | compute_mode baseline | assumption log |
| `__defender_rationale__` | compute_mode baseline | assumption log |
| `__multicloud_rationale__` | compute_mode baseline | assumption log |

## Hot files & their sizes (Oct-2025)

| File | LoC | Notes |
|---|---|---|
| `app.py` | ~1300 | Streamlit orchestrator |
| `src/pillars/data_platform.py` | ~1340 | Largest pillar — Fabric + 7 DB types |
| `src/compute_mode.py` | ~950 | Cross-pillar policy (split candidate) |
| `src/landing_zone/landing_zone.py` | ~880 | LZ component catalog + builder |
| `src/pillars/azure_security.py` | ~720 | Defender + Sentinel + governance |
| `src/pillars/infra_modernization.py` | ~740 | App Service + AKS + DevTools |
| `src/pillars/infra_lift_shift.py` | ~720 | VM extraction + LZ orchestration |
| `src/pillars/ai_application.py` | ~670 | OpenAI + Foundry + AI Search + GPU |
| `src/pillars/auto_simulate.py` | ~450 | Sonnet prompt + schemas |
| `src/analysis/builder.py` | ~370 | VM/disk grouping + emission |
| `src/pillars/ai_model_picker.py` | ~380 | Use-case → 1st/3rd party AI model |

Total ~9300 LoC.
