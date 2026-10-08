import hashlib
import json
from threading import Lock
from time import monotonic


def make_key(payload, base_url, scope=""):
    serialized = json.dumps({"payload": payload, "base_url": base_url, "scope": scope},
                            ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class TTLCache:
    def __init__(self, ttl=600, max_entries=1000, clock=monotonic):
        self.ttl = ttl
        self.max_entries = max_entries
        self.clock = clock
        self._items = {}
        self._lock = Lock()

    def get(self, key):
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires, value = item
            if expires <= self.clock():
                del self._items[key]
                return None
            return value

    def set(self, key, value):
        with self._lock:
            now = self.clock()
            for expired in [k for k, (expires, _) in self._items.items() if expires <= now]:
                del self._items[expired]
            if key not in self._items and len(self._items) >= self.max_entries:
                del self._items[next(iter(self._items))]
            self._items[key] = (now + self.ttl, value)
