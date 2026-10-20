"""Flux Industrial Energy Intelligence: local Python + SQLite application.

Run with: python app.py
All demonstration readings are synthetic and are labeled in the UI.
"""
from __future__ import annotations

import csv
import hashlib
import importlib
import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from datetime import date, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.parse import urlparse as parse_url

from connectors.base import BaseConnector
from connectors.csv_folder import CsvFolderConnector
from connectors.http_json import HttpJsonConnector

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
DB_PATH = Path(os.environ.get("FLUX_DB_PATH", ROOT / "work" / "flux.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
ELECTRICITY_RATE = 7.35  # INR/kWh, illustrative blended tariff
GAS_RATE = 2.85          # INR/kWh, illustrative equivalent tariff
GRID_FACTOR = 0.708      # kg CO2e/kWh, illustrative factor
GAS_FACTOR = 0.202       # kg CO2e/kWh, illustrative factor
SOURCE_LOCK = threading.Lock()


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sites (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, process TEXT NOT NULL,
          electricity_share REAL NOT NULL DEFAULT 0.78,
          gas_share REAL NOT NULL DEFAULT 0.22,
          production_units REAL NOT NULL DEFAULT 10000, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS readings (
          id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL REFERENCES sites(id),
          reading_date TEXT NOT NULL, energy_kwh REAL NOT NULL CHECK(energy_kwh >= 0),
          production_units REAL NOT NULL DEFAULT 0 CHECK(production_units >= 0),
          UNIQUE(site_id, reading_date)
        );
        CREATE INDEX IF NOT EXISTS idx_readings_date ON readings(reading_date, site_id);
        CREATE TABLE IF NOT EXISTS scenario_runs (
          id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          idle_reduction_pct REAL NOT NULL, flexible_shift_pct REAL NOT NULL,
          annual_savings_inr REAL NOT NULL, avoided_tco2e REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS data_sources (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
          config_json TEXT NOT NULL, poll_interval_seconds INTEGER NOT NULL DEFAULT 60,
          enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
          last_checked TEXT, last_success TEXT, last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS ingestion_runs (
          id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES data_sources(id),
          started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL,
          records_received INTEGER NOT NULL DEFAULT 0, records_written INTEGER NOT NULL DEFAULT 0,
          message TEXT
        );
        CREATE TABLE IF NOT EXISTS source_items (
          source_id INTEGER NOT NULL REFERENCES data_sources(id), item_key TEXT NOT NULL,
          processed_at TEXT NOT NULL, PRIMARY KEY(source_id,item_key)
        );
        """)
        if db.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 0:
            sites = [("Pune", "Assembly", .80, .20, 14500),
                     ("Chennai", "Components", .74, .26, 11200),
                     ("Vadodara", "Foundry", .68, .32, 9800),
                     ("Nashik", "Finishing", .84, .16, 12800)]
            db.executemany("INSERT INTO sites(name,process,electricity_share,gas_share,production_units) VALUES(?,?,?,?,?)", sites)
            today = date.today()
            for site_id, base in enumerate((70400, 66100, 73500, 58200), 1):
                for offset in range(119, -1, -1):
                    day = today - timedelta(days=offset)
                    weekly = (-.08 if day.weekday() == 6 else -.035 if day.weekday() == 5 else .025)
                    season = 1 + .045 * math.sin((119-offset) * .17)
                    spike = 1.33 if site_id == 3 and offset == 3 else 1.0
                    energy = round(base * (1 + weekly) * season * spike)
                    units = round((9800 + site_id * 580) * (0 if day.weekday() == 6 else 1) * (1 + .025 * math.sin(offset * .31)))
                    db.execute("INSERT INTO readings(site_id,reading_date,energy_kwh,production_units) VALUES(?,?,?,?)", (site_id, day.isoformat(), energy, units))
        db.execute("""INSERT INTO data_sources(name,kind,config_json,poll_interval_seconds,enabled,created_at)
          SELECT 'Incoming CSV folder','csv_folder',?,15,1,? WHERE NOT EXISTS (SELECT 1 FROM data_sources)""",
          (json.dumps({'folder':'work/inbox','min_file_age_seconds':3}),utc_now()))


def dashboard(days=30):
    days = max(7, min(int(days), 120))
    today = date.today()
    start = today - timedelta(days=days - 1)
    with connect() as db:
        rows = db.execute("""SELECT r.*,s.name,s.process,s.electricity_share,s.gas_share
          FROM readings r JOIN sites s ON s.id=r.site_id
          WHERE r.reading_date>=? AND r.reading_date<=? ORDER BY r.reading_date,r.site_id""", (start.isoformat(), today.isoformat())).fetchall()
        sites = db.execute("SELECT * FROM sites WHERE active=1 ORDER BY id").fetchall()
        hist = db.execute("""SELECT r.*,s.name,s.process,s.electricity_share,s.gas_share
          FROM readings r JOIN sites s ON s.id=r.site_id WHERE r.reading_date>=? ORDER BY r.reading_date,r.site_id""", ((today-timedelta(days=45)).isoformat(),)).fetchall()
    byday = {}
    site_stats = {s['id']: {'energy': 0, 'units': 0, 'name': s['name'], 'process': s['process']} for s in sites}
    total_energy = total_cost = scope2_co2 = electricity_kwh = gas_kwh = 0.0
    for r in rows:
        energy = r['energy_kwh']; elec = energy * r['electricity_share']; gas = energy * r['gas_share']
        cost = elec * ELECTRICITY_RATE + gas * GAS_RATE
        co2 = (elec * GRID_FACTOR + gas * GAS_FACTOR) / 1000
        total_energy += energy; total_cost += cost; scope2_co2 += elec * GRID_FACTOR / 1000
        electricity_kwh += elec; gas_kwh += gas
        byday[r['reading_date']] = byday.get(r['reading_date'], 0) + energy
        stats = site_stats[r['site_id']]; stats['energy'] += energy; stats['units'] += r['production_units']
    series = [{'date': (start + timedelta(days=i)).isoformat(), 'actual_kwh': round(byday.get((start+timedelta(days=i)).isoformat(), 0))} for i in range(days)]
    # Seasonal weekday baseline plus linear drift, fitted from recent daily portfolio totals.
    recent = {}
    for r in hist:
        recent[r['reading_date']] = recent.get(r['reading_date'], 0) + r['energy_kwh']
    pairs = sorted(recent.items())
    mean = sum(v for _, v in pairs) / max(1, len(pairs))
    slope = sum((i-(len(pairs)-1)/2)*(v-mean) for i,(_,v) in enumerate(pairs)) / max(1, sum((i-(len(pairs)-1)/2)**2 for i in range(len(pairs))))
    weekday_factor = {}
    for wd in range(7):
        vals = [v for d,v in pairs if date.fromisoformat(d).weekday() == wd]
        weekday_factor[wd] = sum(vals)/len(vals)/mean if vals and mean else 1
    forecast = []
    for h in range(1, 8):
        d = today + timedelta(days=h)
        value = max(0, mean + slope*(len(pairs)+h-1-(len(pairs)-1)/2)) * weekday_factor[d.weekday()]
        forecast.append({'date': d.isoformat(), 'forecast_kwh': round(value)})
    site_output = []
    for s in sites:
        st = site_stats[s['id']]
        site_output.append({'id':s['id'],'name':s['name'],'process':s['process'], 'energy_kwh':round(st['energy']),
                            'production_units':round(st['units']), 'intensity':round(st['energy']/max(1,st['units']),2)})
    # Detect daily readings over 2.2 standard deviations above the preceding 14 days at each site.
    alerts = []
    for s in sites:
        sr = [r for r in hist if r['site_id'] == s['id']]
        for i in range(max(14, len(sr)-14), len(sr)):
            baseline = [r['energy_kwh'] for r in sr[max(0,i-14):i]]
            if len(baseline) < 7: continue
            avg = sum(baseline)/len(baseline)
            std = math.sqrt(sum((v-avg)**2 for v in baseline)/len(baseline))
            current = sr[i]['energy_kwh']
            if current > avg + 2.2*max(std, avg*.025):
                alerts.append({'site':s['name'],'process':s['process'],'date':sr[i]['reading_date'],
                  'energy_kwh':round(current),'baseline_kwh':round(avg),'deviation_pct':round((current/avg-1)*100,1),
                  'severity':'HIGH' if current > avg + 3*max(std,avg*.025) else 'WATCH'})
    alerts.sort(key=lambda x:x['date'], reverse=True)
    return {'period':{'from':start.isoformat(),'to':today.isoformat(),'days':days},
      'kpis':{'energy_kwh':round(total_energy),'cost_inr':round(total_cost),'emissions_tco2e':round(scope2_co2),'anomaly_count':len(alerts)},
      'series':series,'forecast':forecast,'sites':site_output,'alerts':alerts[-10:],
      'mix':{'electricity_kwh':round(electricity_kwh),'gas_kwh':round(gas_kwh),'onsite_generation_kwh':0},
      'assumptions':{'electricity_rate_inr_kwh':ELECTRICITY_RATE,'gas_rate_inr_kwh':GAS_RATE,'grid_factor_kg_co2e_kwh':GRID_FACTOR,'synthetic_data':True}}


def connector_class(kind):
    builtins = {'csv_folder': CsvFolderConnector, 'http_json': HttpJsonConnector}
    if kind in builtins:
        return builtins[kind]
    if not kind.startswith('custom_') or not kind.replace('_', '').isalnum():
        raise ValueError('Unknown connector kind. Use csv_folder, http_json, or custom_<name>.')
    module = importlib.import_module(f'connectors.{kind}')
    cls = getattr(module, 'Connector', None)
    if cls is None or not issubclass(cls, BaseConnector):
        raise ValueError(f'{kind} must define Connector as a subclass of BaseConnector')
    return cls


def source_view(row):
    return {'id':row['id'],'name':row['name'],'kind':row['kind'],
      'config':json.loads(row['config_json']),'poll_interval_seconds':row['poll_interval_seconds'],
      'enabled':bool(row['enabled']),'created_at':row['created_at'],'last_checked':row['last_checked'],
      'last_success':row['last_success'],'last_error':row['last_error'],
      'status':'error' if row['last_error'] else 'connected' if row['last_success'] else 'waiting'}


def sync_source(source_id):
    # One source sync at a time; protects scheduler and manual refresh from duplicate work.
    if not SOURCE_LOCK.acquire(blocking=False):
        return {'status':'busy','message':'Another source sync is already running.'}
    started=utc_now()
    try:
        with connect() as db:
            source=db.execute('SELECT * FROM data_sources WHERE id=? AND enabled=1',(source_id,)).fetchone()
            if not source: return {'status':'error','message':'Source not found or disabled.'}
            config=json.loads(source['config_json'])
            run_id=db.execute("INSERT INTO ingestion_runs(source_id,started_at,status) VALUES(?,?,'running')",(source_id,started)).lastrowid
            seen={r['item_key'] for r in db.execute('SELECT item_key FROM source_items WHERE source_id=?',(source_id,))}
        connector=connector_class(source['kind'])()
        incoming=connector.fetch(config,seen)
        normalized=[]
        with connect() as db:
            valid_sites={r['id'] for r in db.execute('SELECT id FROM sites')}
        for row in incoming:
            site_id=int(row['site_id']); reading_date=date.fromisoformat(str(row['reading_date'])[:10]).isoformat()
            energy=float(row['energy_kwh']); units=float(row.get('production_units') or 0)
            if site_id not in valid_sites or energy<0 or units<0 or not math.isfinite(energy+units):
                raise ValueError(f'Invalid reading for site_id={site_id}, date={reading_date}')
            raw_key=row.get('_item_key')
            if raw_key is None:
                stable=json.dumps({'site_id':site_id,'reading_date':reading_date,'energy_kwh':energy,'production_units':units},sort_keys=True)
                raw_key=hashlib.sha256(stable.encode()).hexdigest()
            normalized.append((site_id,reading_date,energy,units,str(raw_key)))
        written=0
        with connect() as db:
            prior={r['item_key'] for r in db.execute('SELECT item_key FROM source_items WHERE source_id=?',(source_id,))}
            for site_id,reading_date,energy,units,item_key in normalized:
                if item_key in prior: continue
                db.execute('''INSERT INTO readings(site_id,reading_date,energy_kwh,production_units) VALUES(?,?,?,?)
                  ON CONFLICT(site_id,reading_date) DO UPDATE SET energy_kwh=excluded.energy_kwh,production_units=excluded.production_units''',
                  (site_id,reading_date,energy,units))
                db.execute('INSERT OR IGNORE INTO source_items(source_id,item_key,processed_at) VALUES(?,?,?)',(source_id,item_key,utc_now()))
                prior.add(item_key); written+=1
            completed=utc_now()
            db.execute("UPDATE ingestion_runs SET completed_at=?,status='success',records_received=?,records_written=?,message=? WHERE id=?",
              (completed,len(normalized),written,'No new readings' if written==0 else 'Readings upserted',run_id))
            db.execute('UPDATE data_sources SET last_checked=?,last_success=?,last_error=NULL WHERE id=?',(completed,completed,source_id))
        return {'status':'success','records_received':len(normalized),'records_written':written,'run_id':run_id}
    except Exception as exc:
        message=f'{type(exc).__name__}: {exc}'
        with connect() as db:
            if 'run_id' in locals():
                db.execute("UPDATE ingestion_runs SET completed_at=?,status='error',message=? WHERE id=?",(utc_now(),message,run_id))
            db.execute('UPDATE data_sources SET last_checked=?,last_error=? WHERE id=?',(utc_now(),message,source_id))
        return {'status':'error','message':message}
    finally:
        SOURCE_LOCK.release()


def source_scheduler():
    while True:
        try:
            now=datetime.now(timezone.utc)
            with connect() as db:
                sources=db.execute('SELECT id,last_checked,poll_interval_seconds FROM data_sources WHERE enabled=1').fetchall()
            for src in sources:
                due=True
                if src['last_checked']:
                    try:
                        last=datetime.fromisoformat(src['last_checked'])
                        due=(now-last).total_seconds() >= src['poll_interval_seconds']
                    except ValueError: pass
                if due: sync_source(src['id'])
        except Exception as exc:
            print(f'Ingestion scheduler warning: {exc}')
        time.sleep(5)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUTPUTS), **kwargs)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store')
        self.end_headers(); self.wfile.write(body)

    def read_json(self):
        return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0')) or 0))

    def do_GET(self):
        parsed=urlparse(self.path)
        if parsed.path == '/api/health': return self.send_json({'status':'ok','database':str(DB_PATH),'data':'SQL-backed dashboard with scheduled source connectors'})
        if parsed.path == '/api/dashboard':
            days=parse_qs(parsed.query).get('days',['30'])[0]
            try: return self.send_json(dashboard(days))
            except (ValueError, sqlite3.Error) as exc: return self.send_json({'error':str(exc)},400)
        if parsed.path == '/api/sites':
            with connect() as db: return self.send_json([dict(r) for r in db.execute('SELECT * FROM sites ORDER BY id')])
        if parsed.path == '/api/sources':
            with connect() as db: rows=db.execute('SELECT * FROM data_sources ORDER BY id').fetchall()
            return self.send_json([source_view(r) for r in rows])
        if parsed.path == '/api/ingestion/runs':
            limit=max(1,min(100,int(parse_qs(parsed.query).get('limit',['20'])[0])))
            with connect() as db:
                rows=db.execute('''SELECT r.*,s.name AS source_name FROM ingestion_runs r
                  JOIN data_sources s ON s.id=r.source_id ORDER BY r.id DESC LIMIT ?''',(limit,)).fetchall()
            return self.send_json([dict(r) for r in rows])
        if parsed.path.startswith('/api/'): return self.send_json({'error':'Not found'},404)
        if parsed.path == '/': self.path='/index.html'
        return super().do_GET()

    def do_POST(self):
        parsed=urlparse(self.path)
        try:
            if parsed.path == '/api/readings/import-csv':
                raw=self.rfile.read(int(self.headers.get('Content-Length','0'))).decode('utf-8-sig')
                reader=csv.DictReader(raw.splitlines())
                required={'site_id','reading_date','energy_kwh'}
                if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                    return self.send_json({'error':'CSV header must include site_id, reading_date, energy_kwh; production_units is optional.'},400)
                inserted=0
                with connect() as db:
                    for row in reader:
                        site_id=int(row['site_id']); energy=float(row['energy_kwh']); units=float(row.get('production_units') or 0); rd=date.fromisoformat(row['reading_date']).isoformat()
                        if energy<0 or units<0 or not db.execute('SELECT 1 FROM sites WHERE id=?',(site_id,)).fetchone(): raise ValueError('Invalid site or negative reading value.')
                        db.execute('INSERT INTO readings(site_id,reading_date,energy_kwh,production_units) VALUES(?,?,?,?) ON CONFLICT(site_id,reading_date) DO UPDATE SET energy_kwh=excluded.energy_kwh,production_units=excluded.production_units',(site_id,rd,energy,units)); inserted+=1
                return self.send_json({'upserted':inserted},201)
            if parsed.path.startswith('/api/sources/') and parsed.path.endswith('/sync'):
                try: source_id=int(parsed.path.split('/')[-2])
                except ValueError: return self.send_json({'error':'Invalid source id'},400)
                result=sync_source(source_id)
                return self.send_json(result,200 if result['status'] in ('success','busy') else 400)
            payload=self.read_json()
            if parsed.path == '/api/sources':
                name=str(payload.get('name','')).strip(); kind=str(payload.get('kind','')).strip()
                config=payload.get('config',{})
                interval=int(payload.get('poll_interval_seconds',60))
                if not name or not isinstance(config,dict): return self.send_json({'error':'Source name and configuration are required.'},400)
                if not 15<=interval<=86400: return self.send_json({'error':'Poll interval must be between 15 seconds and 24 hours.'},400)
                connector_class(kind)
                if kind=='csv_folder' and not config.get('folder'): config['folder']='work/inbox'
                if kind=='http_json':
                    url=config.get('url',''); parsed_url=parse_url(url)
                    if parsed_url.scheme not in ('http','https') or not parsed_url.netloc:
                        return self.send_json({'error':'HTTP JSON sources need a valid http:// or https:// URL.'},400)
                with connect() as db:
                    cur=db.execute('INSERT INTO data_sources(name,kind,config_json,poll_interval_seconds,enabled,created_at) VALUES(?,?,?,?,1,?)',
                      (name,kind,json.dumps(config),interval,utc_now()))
                    row=db.execute('SELECT * FROM data_sources WHERE id=?',(cur.lastrowid,)).fetchone()
                return self.send_json(source_view(row),201)
            if parsed.path == '/api/scenarios':
                idle=float(payload['idle_reduction_pct']); shift=float(payload['flexible_shift_pct'])
                if not 0<=idle<=30 or not 0<=shift<=40: return self.send_json({'error':'Idle reduction must be 0–30%; load shift must be 0–40%.'},400)
                with connect() as db:
                    coverage=db.execute("SELECT SUM(energy_kwh),COUNT(DISTINCT reading_date) FROM readings WHERE reading_date>=?",((date.today()-timedelta(days=364)).isoformat(),)).fetchone()
                    kwh=(coverage[0] or 0)*365/max(1,coverage[1])
                    annual_cost=kwh*(.78*ELECTRICITY_RATE+.22*GAS_RATE)
                    savings=annual_cost*(idle/100*.34+shift/100*.16)
                    avoided=savings*((.78*GRID_FACTOR+.22*GAS_FACTOR)/1000)/((.78*ELECTRICITY_RATE+.22*GAS_RATE))
                    cur=db.execute('INSERT INTO scenario_runs(idle_reduction_pct,flexible_shift_pct,annual_savings_inr,avoided_tco2e) VALUES(?,?,?,?)',(idle,shift,savings,avoided))
                return self.send_json({'id':cur.lastrowid,'annual_savings_inr':round(savings),'avoided_tco2e':round(avoided,1),'assumptions':'Directional estimate using demo portfolio tariff and emissions factors.'})
            if parsed.path == '/api/readings/import':
                rows=payload.get('readings')
                if not isinstance(rows,list) or not rows: return self.send_json({'error':'Provide a non-empty readings array.'},400)
                inserted=0
                with connect() as db:
                    for row in rows:
                        site_id=int(row['site_id']); energy=float(row['energy_kwh']); units=float(row.get('production_units',0)); rd=date.fromisoformat(row['reading_date']).isoformat()
                        if energy<0 or units<0 or not db.execute('SELECT 1 FROM sites WHERE id=?',(site_id,)).fetchone(): raise ValueError('Invalid site or negative reading value.')
                        db.execute('INSERT INTO readings(site_id,reading_date,energy_kwh,production_units) VALUES(?,?,?,?) ON CONFLICT(site_id,reading_date) DO UPDATE SET energy_kwh=excluded.energy_kwh,production_units=excluded.production_units',(site_id,rd,energy,units)); inserted+=1
                return self.send_json({'upserted':inserted},201)
            return self.send_json({'error':'Not found'},404)
        except (KeyError,TypeError,ValueError,json.JSONDecodeError,sqlite3.Error) as exc:
            return self.send_json({'error':str(exc)},400)


if __name__ == '__main__':
    initialize()
    threading.Thread(target=source_scheduler,name='flux-source-scheduler',daemon=True).start()
    server=ThreadingHTTPServer(('127.0.0.1',8000),Handler)
    print(f'Flux is running at http://127.0.0.1:8000  (SQLite: {DB_PATH})')
    try: server.serve_forever()
    except KeyboardInterrupt: print('\nFlux stopped.')
    finally: server.server_close()
