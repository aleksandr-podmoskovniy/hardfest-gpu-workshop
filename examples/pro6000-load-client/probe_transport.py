"""Bounded, observable HTTP transport for the private streaming load probe."""
import http.client
import socket
import threading
import urllib.request

_local = threading.local()
_lock = threading.Lock()
_sockets = {}
_aborted = {}

def bind_session(session):
    _local.session = session

def release_session(session):
    with _lock:
        _sockets.pop(session, None)

def abort_session(session, reason):
    with _lock:
        _aborted.setdefault(session, reason)
        sock = _sockets.get(session)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

def abort_reason(session):
    with _lock:
        return _aborted.get(session)

class KeepAliveHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Linux probe containers. Do not silently claim this transport on other OSes.
        for option, value in [('TCP_KEEPIDLE', 60), ('TCP_KEEPINTVL', 20), ('TCP_KEEPCNT', 3)]:
            self.sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, option), value)
        session = getattr(_local, 'session', None)
        if session is not None:
            with _lock:
                _sockets[session] = self.sock
                cancelled = session in _aborted
            if cancelled:
                abort_session(session, abort_reason(session))

class KeepAliveHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(KeepAliveHTTPConnection, req)

def make_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), KeepAliveHandler())

class IdleEngineWatchdog:
    """Only fresh, successful, complete engine metrics establish idle time."""
    def __init__(self, grace_seconds=120):
        self.grace_seconds = grace_seconds
        self.idle_since = None

    def observe(self, sample, now, pending):
        metrics = sample.get('metrics', {})
        groups = [[v for k, v in metrics.items() if k.startswith('vllm:' + name + '{')]
                  for name in ('num_requests_running', 'num_requests_waiting')]
        fresh = 0 <= now - sample.get('unix_time', 0) <= 30
        idle = pending and fresh and not sample.get('error') and all(groups) and all(v == 0 for group in groups for v in group)
        if not idle:
            self.idle_since = None
            return False
        if self.idle_since is None:
            self.idle_since = now
        return now - self.idle_since >= self.grace_seconds
