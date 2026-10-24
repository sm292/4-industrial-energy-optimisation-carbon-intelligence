# Industrial Energy Optimisation & Carbon Intelligence

Python | Industrial Analytics | Energy Efficiency | Carbon Accounting | Optimization

# Flux · Industrial energy intelligence

A local Python + SQLite application with a browser dashboard for factory energy use, demand forecasting, abnormal consumption, cost and emissions estimates, and savings scenarios. No third-party Python packages are required.

## Run locally

1. Install Python 3.10 or later.
2. Double-click `start.bat`, or run `python app.py` from this folder.
3. Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

On first start, the app creates `work/flux.db`, creates four example sites and 120 days of synthetic readings, registers a watcher for `work/inbox`, and starts the local web server. The source scheduler checks enabled sources on their configured intervals (minimum 15 seconds). The dashboard refreshes from SQL every 30 seconds. Stop the server with Ctrl+C in its console.

## Data and database

SQLite is the default SQL database. The schema is in `schema.sql`; `app.py` initializes it automatically. Set `FLUX_DB_PATH` to store the database elsewhere. Tables:

- `sites`: site, process, energy split, and active state.
- `readings`: daily energy and production; one record per site and date.
- `scenario_runs`: saved slider assumptions and resulting estimates.
- `data_sources`: connector type, non-secret configuration, polling interval, and latest sync state.
- `ingestion_runs`: one audit row per automatic or manual source check.
- `source_items`: idempotency keys, so repeated polling does not create repeated imports.

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

### Automatic sources

The **Data sources & automatic updates** section in the dashboard lets you register connectors and check their sync status. The default **Incoming CSV folder** watches `work/inbox`. Drop complete `.csv` files there using columns `site_id,reading_date,energy_kwh,production_units`; Flux waits until the file has stopped changing, imports it, records the run, and skips already processed rows on later checks. For files copied by another process, write as `.tmp` and rename to `.csv` only after the copy completes. Readings are upserted by `(site_id, reading_date)`, so a corrected file can update a prior daily value.

To try a direct portal upload, download [outputs/demo_readings.csv](outputs/demo_readings.csv), open the dashboard's **Data sources & automatic updates** section, choose the file under **Upload meter readings CSV**, then click **Upload readings to SQL**. The demo contains 120 rows covering 30 days across the four seeded sites, including deliberate spikes for the anomaly view. Re-uploading it is safe: matching site/date readings are updated in place.

The same readings are also available as [outputs/demo_readings.json](outputs/demo_readings.json). Choose it under **Upload meter readings JSON** to import through the JSON API.

The built-in `http_json` connector polls a GET endpoint that returns either an array of readings or an object with a `readings` array. Register it in the dashboard using configuration such as:

```json
{"url":"https://meter.example/api/readings","records_path":"readings","token_env":"METER_API_TOKEN","timeout_seconds":15}
```

Set the named token environment variable before starting Flux; the secret itself is not stored in SQLite. The API records must use `site_id`, `reading_date`, `energy_kwh`, and optionally `production_units`. If the provider's response uses different field names, add a small Python adapter to map it.

### Add a custom Python source

Implement the connector contract in `connectors/custom_<name>.py`, using [connectors/custom_example.py.example](connectors/custom_example.py.example) as a starting point. Name the class `Connector`, subclass `BaseConnector`, and implement `fetch(config, seen_item_keys)` to return normalized readings. Give each record a stable `_item_key` when possible. Then add a source in the dashboard with kind `custom_<name>` and your adapter's JSON configuration. Restart Flux after adding a new adapter file. This lets you integrate a historian, MES, utility portal, database client, or vendor SDK without changing the dashboard or ingestion scheduler.

## API

- `GET /api/health` — service status and database path.
- `GET /api/sites` — configured sites.
- `GET /api/dashboard?days=30` — KPI totals, daily series, seven-day forecast, site intensity, and detected anomalies. `days` accepts 7–120.
- `POST /api/scenarios` — JSON `{ "idle_reduction_pct": 12, "flexible_shift_pct": 20 }`; returns estimates and records the scenario.
- `POST /api/readings/import` — JSON `{ "readings": [{"site_id":1,"reading_date":"2026-10-18","energy_kwh":70400,"production_units":15020}] }`.
- `POST /api/readings/import-csv` — CSV file upload as shown above.
- `GET /api/sources` — registered connectors and latest sync status.
- `POST /api/sources` — register `{ "name":"Meter feed", "kind":"csv_folder", "config":{"folder":"work/inbox"}, "poll_interval_seconds":60 }`; `http_json` accepts a `url`, optional `records_path`, `token_env`, and `timeout_seconds`.
- `POST /api/sources/{id}/sync` — run a source check immediately.
- `GET /api/ingestion/runs?limit=20` — recent import history and counts.

## Calculation notes

The seven-day outlook uses a linear trend on recent daily portfolio totals adjusted by weekday averages. Daily abnormal readings are flagged when they exceed the site's preceding 14-day mean by 2.2 standard deviations (with a small baseline floor). Cost and emissions use the illustrative electricity and gas rates and factors in `app.py`. Savings are directional scenario estimates, not verified savings. New imported data updates the dashboard automatically, but synthetic seed readings remain for dates and sites that have not been replaced. The service binds to localhost and is intended as a local demonstration, not an internet-facing deployment.
