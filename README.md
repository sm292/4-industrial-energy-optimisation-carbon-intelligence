# Flux · Industrial energy intelligence

A local Python + SQLite application with a browser dashboard for factory energy use, demand forecasting, abnormal consumption, cost and emissions estimates, and savings scenarios. No third-party Python packages are required.

## Run locally

1. Install Python 3.10 or later.
2. Double-click `start.bat`, or run `python app.py` from this folder.
3. Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

On first start, the app creates `work/flux.db`, creates four example sites and 120 days of synthetic readings, and starts the local web server. The dashboard's data connection indicator changes to **SQL DATA CONNECTED** when the API responds. Stop the server with Ctrl+C in its console.

## Data and database

SQLite is the default SQL database. The schema is in `schema.sql`; `app.py` initializes it automatically. Set `FLUX_DB_PATH` to store the database elsewhere. Tables:

- `sites`: site, process, energy split, and active state.
- `readings`: daily energy and production; one record per site and date.
- `scenario_runs`: saved slider assumptions and resulting estimates.

The `/api/sites` endpoint lists site IDs and names. Upload a CSV to `POST /api/readings/import-csv` with columns `site_id,reading_date,energy_kwh,production_units` (date format `YYYY-MM-DD`; production is optional). Example:

```csv
site_id,reading_date,energy_kwh,production_units
1,2026-10-18,70400,15020
2,2026-10-18,66100,11930
```

CSV rows are inserted or updated by site and date. From PowerShell, for example:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/readings/import-csv -ContentType text/csv -InFile .\readings.csv
```

## API

- `GET /api/health` — service status and database path.
- `GET /api/sites` — configured sites.
- `GET /api/dashboard?days=30` — KPI totals, daily series, seven-day forecast, site intensity, and detected anomalies. `days` accepts 7–120.
- `POST /api/scenarios` — JSON `{ "idle_reduction_pct": 12, "flexible_shift_pct": 20 }`; returns estimates and records the scenario.
- `POST /api/readings/import` — JSON `{ "readings": [{"site_id":1,"reading_date":"2026-10-18","energy_kwh":70400,"production_units":15020}] }`.
- `POST /api/readings/import-csv` — CSV file upload as shown above.

## Calculation notes

The seven-day outlook uses a linear trend on recent daily portfolio totals adjusted by weekday averages. Daily abnormal readings are flagged when they exceed the site's preceding 14-day mean by 2.2 standard deviations (with a small baseline floor). Cost and emissions use the illustrative electricity and gas rates and factors in `app.py`. Savings are directional scenario estimates, not verified savings. Replace those factors and the synthetic seed readings with approved site data before operational decisions. The service binds to localhost and is intended as a local demonstration, not an internet-facing deployment.
