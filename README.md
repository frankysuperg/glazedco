# NovaTrend (Road To Tech 2026 | Hackathon Project)

**An end-to-end analytics platform for a multi-store retail chain: data cleaning, demand forecasting, replenishment recommendations, anomaly detection, a read-only natural-language SQL assistant, and a live dashboard.**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)
![scikit-learn](https://img.shields.io/badge/scikit--learn-F7931E?logo=scikitlearn&logoColor=white)
![Gemini](https://img.shields.io/badge/Google%20Gemini-8E75B2?logo=googlegemini&logoColor=white)
![FastMCP](https://img.shields.io/badge/FastMCP-3-black)

---

## Overview

**Glazed Co.** is a fictional U.S. chain of donut, coffee and bakery stores located in airports, train stations, bus terminals and a resort. The dataset is **100% synthetic** (USD, 2025-10-01 to 2026-09-30) and covers **20 sites and 38 stores**. It deliberately includes real-world messiness: store openings, closures and temporary shutdowns, perishables with 1-3 day shelf lives, stock-outs, waste, and late or short supplier deliveries.

This project turns that raw data into decisions. It cleans the data, forecasts weekly demand, recommends what to reorder, flags anomalies, answers business questions in plain language, and presents everything in a dashboard that can be filtered by store.

> The dataset was provided for a hackathon and is for that use only. It must not be redistributed or presented as real data.

## Key Features

| Module | What it does |
|---|---|
| **Data cleaning** (`glazedco.cleaning`) | Reads 13 tables with `SELECT` only, quarantines invalid rows with an explicit reason, flags suspicious ones, and writes a cleaning report plus an optional Parquet cache. |
| **Metrics** (`glazedco.metrics`) | Net revenue, COGS and gross margin, with amount and percentage kept separate and a null percentage when revenue is zero. |
| **Forecasting** (`glazedco.forecast`) | Weekly SKU/store forecasts for 1-4 weeks using rolling-origin time-series validation. Compares a seasonal-naive baseline, a 4-week moving average, Croston/SBA for intermittent demand, and a Ridge regression with lag features. |
| **Anomaly detection** (`glazedco.anomalies`) | Robust daily anomaly alerts by metric, annotated with context (promotions, holidays, store closures) and an assessment of how adjusting for anomalies affects forecast error. |
| **Replenishment** (`glazedco.replenishment`) | Order recommendations with safety stock, supplier lead time and fill rate estimated from real purchase orders, case-size lots, shelf life and optional budget limits. |
| **SQL assistant** (`glazedco.sql_assistant`) | Natural-language questions answered through Gemini and an MCP server, with a strict read-only SQL validator and explanations built only from returned cells. |
| **Strategy proposals** (`glazedco.strategies`) | Marketing ideas that connect trending TikTok hashtags (via TrendsAPI) to verified catalog and margin facts. |
| **Dashboard** (`glazedco.api`) | A FastAPI backend and a self-contained HTML dashboard with a store filter, KPIs, charts, alerts and an AI chat. |

## Design Principles

- **Never invent data.** Missing values stay missing and are shown as "No disponible" rather than imputed.
- **Separate observed sales from true demand.** Stock-out days and store closures are treated as *censored* demand and are excluded from training, not counted as zero.
- **No data leakage.** Models are validated with rolling-origin time splits; features use only information available at forecast time.
- **Correlation is not trend.** An anomaly is never presented as a trend without evidence, and context is a recorded coincidence, not a causal claim.
- **Read-only by construction.** No module modifies the database schema or data.
- **Verifiable AI.** Gemini explanations are restricted to references to returned cells, so every number shown comes from the query result.
- **Secrets stay in the environment.** Credentials are read from variables, never logged, and redacted from error output.

## Architecture

```
                   ┌──────────────────────────┐
                   │ PostgreSQL / Tiger Data  │  13 core tables (read-only)
                   └────────────┬─────────────┘
                                │ SELECT
                                ▼
                   ┌──────────────────────────┐
                   │  cleaning  →  Parquet    │  clean tables + quarantine
                   └────────────┬─────────────┘
          ┌─────────────┬───────┴───────┬───────────────┐
          ▼             ▼               ▼               ▼
      metrics       forecast        anomalies     replenishment
          │             │               │               │
          └─────────────┴───────┬───────┴───────────────┘
                                ▼
                   ┌──────────────────────────┐        ┌───────────────┐
                   │   FastAPI + dashboard    │◄──────►│ Gemini / MCP  │
                   │  (precomputed at start)  │        │ SQL assistant │
                   └──────────────────────────┘        │ + TrendsAPI   │
                                                       └───────────────┘
```

```
glazedco/
├── cleaning/        # SELECT-only ingestion, quarantine rules, reports
├── metrics/         # revenue, COGS, gross margin
├── forecast/        # baselines, Croston/SBA, Ridge, temporal validation
├── anomalies/       # robust detection and context enrichment
├── replenishment/   # demand, lead time, safety stock, recommendations
├── sql_validator.py # AST-based SELECT-only validator (sqlglot)
├── sql_assistant/   # FastMCP server and Gemini question flow
├── gemini/          # client, profiles and versioned prompts
├── social/          # TrendsAPI client with caching and rate limiting
├── strategies/      # evidence-based campaign proposals
├── api/             # FastAPI app and static dashboard
└── db.py            # pooled, read-only PostgreSQL access
docs/                # design notes for each module
reports/             # generated evaluation reports
tests/               # pytest suite
```

## Selected Results

These figures come from the reports generated on the full dataset (`reports/`).

- **Cleaning:** 589,602 sales rows read, **581,021 retained** and 8,581 quarantined; **90.6%** of retained revenue has a known unit cost.
- **Forecast accuracy (weekly, horizons 1-4):** the Ridge model achieved a WAPE of about **11.4% at the chain total** and **14.5% per store**, compared with 17.0% and 18.9% for the seasonal-naive baseline.
- **Replenishment:** 2,169 forecast series evaluated, 1,414 with a recommendation, and 265 SKU/store pairs at **high stock-out risk** at the cut-off date (2026-09-30).
- **Anomalies:** 44,582 daily alerts across four metrics; 14,187 have no identified contextual cause. Smoothing anomalies in the training history did *not* improve forecast error (a change of under 0.4 percentage points), so the default policy is to keep them.

## Getting Started

### Prerequisites

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Read access to the project's PostgreSQL database (Tiger Data)
- *Optional:* a Google Gemini API key and a TrendsAPI key

### Installation

```bash
git clone <repository-url>
cd glazedco
uv sync
cp .env.example .env     # then fill in your values
```

### Configuration

Set these variables in `.env`. The file is git-ignored, and `.env.example` contains no real values.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (read access) |
| `DATABASE_URL_READONLY` | Preferred dedicated read-only role for the SQL assistant |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Gemini access for explanations and SQL generation (no default model) |
| `TRENDSAPI_KEY` | TikTok trending hashtags for strategy proposals |
| `SQL_MAX_ROWS` | Row limit for assistant queries (default `1000`) |
| `SQL_STATEMENT_TIMEOUT_MS` | Assistant statement timeout (default `5000`) |
| `SQL_LOAD_TIMEOUT_MS` | Timeout for bulk loads (default `300000`) |
| `API_CLEAN_CACHE_MAX_AGE_SECONDS` | Maximum Parquet cache age (default `86400`; `0` forces a reload) |

### Run the pipeline

```bash
uv run python -m glazedco.db_check              # verify connectivity and read-only access
uv run python -m glazedco.cleaning --cache      # clean data, write report and Parquet cache
uv run python -m glazedco.forecast              # forecasts and evaluation report
uv run python -m glazedco.anomalies             # anomaly alerts and impact report
uv run python -m glazedco.replenishment         # replenishment recommendations
```

Each module is a standalone command-line tool; run it with `--help` to see its options.

### Start the dashboard

```bash
uv run uvicorn glazedco.api.app:app --host 127.0.0.1 --port 8000
```

- Dashboard: <http://127.0.0.1:8000/>
- Interactive API docs: <http://127.0.0.1:8000/docs>

The server starts immediately and prepares data in a background worker. Until it finishes, `/api/estado` reports its progress and other API routes return `503` with a `Retry-After` header.

### Ask questions in natural language

```bash
# MCP server over stdio
uv run python -m glazedco.sql_assistant

# One-off question through Gemini
uv run python -m glazedco.sql_assistant --question "What is net revenue by store?"
```

## API Reference

Every route accepts `?sucursal=<store_id>`. Omitting it returns the *All stores* view, and an unknown store returns `404`. List endpoints support `limite` and `desplazamiento` for pagination.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/estado` | Readiness and safe diagnostic detail |
| `GET` | `/api/dashboard/resumen` | Margin, aggregated forecast and alert counts |
| `GET` | `/api/margen` | Gross margin amount and percentage, coverage and exclusions |
| `GET` | `/api/pronostico/semanal` | Weekly forecast with a comparison to the previous four weeks |
| `GET` | `/api/reabastecimiento` | Replenishment recommendations |
| `GET` | `/api/anomalias` | Historical anomalies |
| `GET` | `/api/alertas` | Stock-out and overstock alerts per SKU and store |
| `GET` | `/api/sucursales` | Store search |
| `POST` | `/api/asistente` | Ask a business question in natural language |
| `POST` | `/api/tendencias/estrategias` | Generate hashtag-based strategy proposals |

## SQL Safety Model

The assistant is designed so a model mistake cannot change data:

1. **Validation:** an AST-based validator (sqlglot) accepts only `SELECT` statements and a closed list of functions, and rejects writes (including inside CTEs), multiple statements and unknown constructs.
2. **Schema allow-list:** only the 13 core tables are exposed; staging and rejected-row tables are excluded.
3. **Enforced limits:** the validator forces an outer `LIMIT`, and a statement timeout is applied.
4. **Read-only execution:** queries run in a `READ ONLY` transaction that is always rolled back, with a restricted `search_path`.
5. **Least privilege:** `docs/db_readonly_role.sql` provides a script for a dedicated database role. It has not been applied automatically.

## Testing

```bash
uv run pytest -m "not db"      # unit tests (database and Gemini are mocked)
uv run pytest -m db            # read-only integration tests; requires DATABASE_URL
```

The suite covers cleaning rules, forecasting, anomalies, replenishment, the SQL validator and assistant, Gemini and TrendsAPI clients, API contracts, startup behavior, and the dashboard HTML.

## Known Limitations

- Replenishment selects a unit price per SKU without distinguishing the store, so budget prioritization in the chain-wide view can be affected by price differences.
- Stock-outs identify censored demand but do not quantify lost sales.
- Expiry alerts, reorder points and forecast uncertainty bands are not part of the current API contract and appear as *not available* in the dashboard.
- Strategy proposals currently run in demo mode: strict post-validation is disabled, and each proposal is labeled `gemini` or `respaldo` (template fallback).
- Dashboard data is held in memory; restart the API after publishing new reports or cache files.

## Documentation

Detailed design notes live in [`docs/`](docs/): [dataset context](docs/dataset_context.md), [data audit](docs/data_audit.md), [cleaning report](docs/cleaning_report.md), [database](docs/database.md), [API](docs/api.md), [Gemini layer](docs/gemini.md), [SQL validator](docs/sql_validator.md), [SQL assistant](docs/sql_assistant.md), [social trends](docs/social.md) and [strategies](docs/strategies.md).

## License

No license has been specified. The dataset is synthetic and restricted to hackathon use.
