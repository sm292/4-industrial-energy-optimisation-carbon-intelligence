"""Poll a JSON API. Bearer credentials are read from an environment variable."""
import hashlib
import json
import os
from urllib.request import Request, urlopen

from .base import BaseConnector


class HttpJsonConnector(BaseConnector):
    def fetch(self, config, seen_item_keys):
        url = config['url']
        headers = {'Accept': 'application/json', 'User-Agent': 'FluxEnergy/1.0'}
        token_env = config.get('token_env', '').strip()
        if token_env:
            token = os.environ.get(token_env)
            if not token:
                raise ValueError(f"Environment variable {token_env} is not set")
            headers['Authorization'] = f'Bearer {token}'
        req = Request(url, headers=headers)
        with urlopen(req, timeout=min(60, max(2, int(config.get('timeout_seconds', 15))))) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload
            for part in config.get('records_path', 'readings').split('.'):
                items = items[part]
        else:
            raise ValueError('JSON API response must be an array or an object containing a readings array')
        if not isinstance(items, list):
            raise ValueError('Configured JSON records path must point to an array')
        records = []
        for row in items:
            canonical = json.dumps(row, sort_keys=True, separators=(',', ':'))
            key = hashlib.sha256(canonical.encode()).hexdigest()
            if key not in seen_item_keys:
                records.append({**row, '_item_key': key})
        return records
