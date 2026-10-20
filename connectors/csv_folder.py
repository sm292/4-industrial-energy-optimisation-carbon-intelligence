"""Poll a folder for stable CSV files and normalize their reading rows."""
import csv
import hashlib
import time
from pathlib import Path

from .base import BaseConnector


class CsvFolderConnector(BaseConnector):
    def fetch(self, config, seen_item_keys):
        folder = Path(config['folder']).expanduser()
        if not folder.is_absolute():
            folder = Path(__file__).resolve().parents[1] / folder
        folder.mkdir(parents=True, exist_ok=True)
        min_age = max(1, int(config.get('min_file_age_seconds', 3)))
        records = []
        for path in sorted(folder.glob('*.csv')):
            if time.time() - path.stat().st_mtime < min_age:
                continue
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            file_key = f"{path.name}:{digest}"
            if any(key.startswith(file_key + ':row:') for key in seen_item_keys):
                continue
            text = content.decode('utf-8-sig')
            reader = csv.DictReader(text.splitlines())
            required = {'site_id', 'reading_date', 'energy_kwh'}
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                raise ValueError(f"{path.name}: CSV requires site_id, reading_date, energy_kwh headers")
            for line, row in enumerate(reader, start=2):
                if not row.get('site_id'):
                    continue
                records.append({**row, '_item_key': f'{file_key}:row:{line}'})
        return records
