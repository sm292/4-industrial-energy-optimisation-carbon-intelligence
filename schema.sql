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
