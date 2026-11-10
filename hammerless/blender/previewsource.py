"""Bounded exact-content memo for imported preview source revisions."""
import hashlib
import sys


class SourceRevision:
    def __init__(self, max_bytes=64*1024*1024):
        self.max_bytes=max_bytes
        self.raw=None
        self.digest=None
        self.hashes=0

    def update(self, raw):
        if raw is None:
            self.raw=None;self.digest=None
            return None
        if self.raw is not None and raw==self.raw:
            return self.digest
        digest=hashlib.sha256(raw.encode()).digest()
        self.hashes+=1
        # One current string only, bounded by actual Python string storage.
        # Oversized sources retain the original hash-every-update behavior.
        self.raw=raw if sys.getsizeof(raw)<=self.max_bytes else None
        self.digest=digest
        return digest
