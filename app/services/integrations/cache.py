"""Disposable byte caches. All public-value validation lives in public_catalog.py."""
from collections import OrderedDict
from threading import RLock
import time


class CacheUnavailable(Exception):
    pass


class MemoryCache:
    backend='memory'
    def __init__(self,max_bytes=16000000,max_entries=1000,clock=time.monotonic):
        self.max_bytes=max_bytes; self.max_entries=max_entries; self.clock=clock
        self.entries=OrderedDict(); self.bytes=0; self.lock=RLock()

    def _delete(self,key):
        old=self.entries.pop(key,None)
        if old is not None: self.bytes-=len(old[1])+len(key.encode())

    def get(self,key):
        with self.lock:
            old=self.entries.get(key)
            if old is None: return None
            if old[0]<=self.clock():
                self._delete(key); return None
            self.entries.move_to_end(key)
            return old[1]

    def set(self,key,value,ttl):
        cost=len(value)+len(key.encode())
        if not 0<len(value)<=65536 or cost>self.max_bytes or ttl<=0: return
        with self.lock:
            for stale in [k for k,v in self.entries.items() if v[0]<=self.clock()]: self._delete(stale)
            self._delete(key)
            while self.entries and (self.bytes+cost>self.max_bytes or len(self.entries)>=self.max_entries):
                self._delete(next(iter(self.entries)))
            self.entries[key]=(self.clock()+ttl,value); self.bytes+=cost

    def delete(self,key):
        with self.lock: self._delete(key)


class RedisCache:
    backend='redis'
    def __init__(self,url):
        import redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry
        self.client=redis.Redis.from_url(url,socket_connect_timeout=.2,socket_timeout=.2,
            decode_responses=False,retry=Retry(NoBackoff(),0))
        self.failed_until=0

    def _call(self,fn,*args,**kwargs):
        import redis
        if time.monotonic()<self.failed_until: raise CacheUnavailable()
        try:
            return fn(*args,**kwargs)
        except redis.RedisError:
            self.failed_until=time.monotonic()+5
            raise CacheUnavailable() from None

    def get(self,key): return self._call(self.client.get,key)
    def set(self,key,value,ttl):
        if not 0<len(value)<=65536 or ttl<=0: return
        self._call(self.client.set,key,value,ex=max(1,int(ttl)))
    def delete(self,key): self._call(self.client.delete,key)


class DisabledCache:
    backend='disabled'
    def get(self,key): return None
    def set(self,key,value,ttl): pass
    def delete(self,key): pass
