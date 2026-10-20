-- Flux energy intelligence SQLite schema (also initialized by app.py)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sites (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  process TEXT NOT NULL,
  electricity_share REAL NOT NULL DEFAULT 0.78,
  gas_share REAL NOT NULL DEFAULT 0.22,
  production_units REAL NOT NULL DEFAULT 10000,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS readings (
  id INTEGER PRIMARY KEY,
  site_id INTEGER NOT NULL REFERENCES sites(id),
  reading_date TEXT NOT NULL,
  energy_kwh REAL NOT NULL CHECK (energy_kwh >= 0),
  production_units REAL NOT NULL DEFAULT 0 CHECK (production_units >= 0),
  UNIQUE (site_id, reading_date)
);
CREATE INDEX IF NOT EXISTS idx_readings_date ON readings(reading_date, site_id);

CREATE TABLE IF NOT EXISTS scenario_runs (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  idle_reduction_pct REAL NOT NULL,
  flexible_shift_pct REAL NOT NULL,
  annual_savings_inr REAL NOT NULL,
  avoided_tco2e REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS data_sources (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  config_json TEXT NOT NULL,
  poll_interval_seconds INTEGER NOT NULL DEFAULT 60,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  last_checked TEXT,
  last_success TEXT,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES data_sources(id),
  started_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL,
  records_received INTEGER NOT NULL DEFAULT 0,
  records_written INTEGER NOT NULL DEFAULT 0,
  message TEXT
);

CREATE TABLE IF NOT EXISTS source_items (
  source_id INTEGER NOT NULL REFERENCES data_sources(id),
  item_key TEXT NOT NULL,
  processed_at TEXT NOT NULL,
  PRIMARY KEY (source_id, item_key)
);
