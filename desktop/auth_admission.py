"""Bound work accepted by unauthenticated device and browser sign-in routes."""
import asyncio
from collections import OrderedDict, deque
import ipaddress
import os
import time

WINDOW_SECONDS = 60
MAX_SOURCES = 2048


def network_key(request):
    # Share the gateway's explicit trusted Railway ingress setting.
    if os.getenv("VELIA_WEB_GUEST_TRUST_RAILWAY_IP") == "true":
        edge = request.headers.getall("X-Real-IP", ())
        if len(edge) == 1:
            try:
                return _address_key(edge[0])
            except ValueError:
                pass
    try:
        return _address_key(request.remote)
    except ValueError:
        return "unknown"


def _address_key(value):
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv4Address):
        return str(address)
    if address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(ipaddress.IPv6Network((address, 64), strict=False))


class AuthAdmission:
    def __init__(self, attempts, concurrent):
        self.attempts = attempts
        self.slots = asyncio.Semaphore(concurrent)
        self.sources = OrderedDict()

    def admit(self, request):
        if self.slots.locked():
            return False
        now = time.monotonic()
        cutoff = now - WINDOW_SECONDS
        while self.sources and next(iter(self.sources.values()))[-1] <= cutoff:
            self.sources.popitem(last=False)
        source = network_key(request)
        recent = self.sources.get(source)
        if recent is None:
            if len(self.sources) >= MAX_SOURCES:
                self.sources.popitem(last=False)
            recent = self.sources[source] = deque()
        while recent and recent[0] <= cutoff:
            recent.popleft()
        if len(recent) >= self.attempts:
            return False
        recent.append(now)
        self.sources.move_to_end(source)
        return True
