"""Cached, rate-limited HTTP with host backoff and request deduplication."""
from collections import Counter
import hashlib
import json
import threading
import time
from urllib.parse import urlparse
import requests
from matti.state import atomic_json

class Client:
    def __init__(self, cache, timeout=15, delay=.5, retry=False):
        self.cache, self.timeout, self.delay, self.retry = cache, timeout, delay, retry
        self.lock = threading.Lock()
        self.next_request = {}
        self.failures = Counter()
        self.errors = Counter()
        self.key_locks = {}
        self.last_failure = {}

    def get(self, url, params=None, headers=None):
        key = hashlib.sha256(json.dumps([url, params, headers], sort_keys=True).encode()).hexdigest()
        with self.lock:
            lock = self.key_locks.setdefault(key, threading.Lock())
        with lock:
            return self._get(url, params, headers)

    def _get(self, url, params=None, headers=None):
        key = hashlib.sha256(json.dumps([url, params, headers], sort_keys=True).encode()).hexdigest()
        saved = self.cache / (key + '.json')
        if saved.exists() and not self.retry:
            try:
                data = json.loads(saved.read_text(encoding='utf-8'))
                if time.time() - data['time'] < 30 * 86400:
                    return data['body'], data['url']
            except (ValueError, OSError, KeyError):
                pass
        host = urlparse(url).hostname
        with self.lock:
            if self.failures[host] >= 5:
                if time.monotonic() - self.last_failure.get(host, 0) < 60:
                    self.errors[f'{host}: temporarily paused'] += 1
                    return None, url
                self.failures[host] = 0
            wait = max(0, self.next_request.get(host, 0) - time.monotonic())
            self.next_request[host] = time.monotonic() + wait + self.delay
        if wait:
            time.sleep(wait)
        try:
            response = requests.get(url, params=params, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; LocalMusicLyrics/1.0)', **(headers or {})},
                timeout=(5, self.timeout))
            if response.status_code in (404, 204):
                body = None
            else:
                response.raise_for_status()
                response.encoding = 'utf-8'
                body = response.text
            with self.lock:
                self.failures[host] = 0
            atomic_json(saved, {'time': time.time(), 'url': response.url, 'body': body})
            return body, response.url
        except requests.RequestException as error:
            with self.lock:
                self.failures[host] += 1
                self.last_failure[host] = time.monotonic()
                status = error.response.status_code if error.response is not None else None
                self.errors[f'{host}: {type(error).__name__}' + (f' {status}' if status else '')] += 1
                if status == 429:
                    # Back off the whole host instead of retrying each song.
                    self.failures[host] = 5
                    self.next_request[host] = max(self.next_request.get(host, 0), time.monotonic() + 60)
            return None, url

    def json(self, url, params=None):
        body, source = self.get(url, params)
        if not body:
            return None, source
        try:
            return json.loads(body), source
        except ValueError:
            return None, source


