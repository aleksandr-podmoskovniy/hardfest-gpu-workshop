"""Loopback-only endpoint binding checks; no cluster or GPU access."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

SOURCE = Path(__file__).resolve().parents[1] / 'examples/capacity-load-client/probe_transport.py'
SPEC = importlib.util.spec_from_file_location('probe_transport_endpoint_test', SOURCE)
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)


class EndpointHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.paths.append((self.command, self.path))
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        if self.path.startswith('/redirect/'):
            self.send_response(int(self.path.rsplit('/', 1)[-1]))
            self.send_header('Location', '/other-model')
            body = b''
        else:
            self.send_response(200)
            body = b'{"ok":true}'
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET

    def log_message(self, *_):
        pass


class EndpointBindingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), EndpointHandler)
        cls.server.paths = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.server.paths.clear()
        # Only transport routing is under test, not Linux TCP keepalive knobs.
        self.connection = patch.object(transport, 'KeepAliveHTTPConnection', http.client.HTTPConnection)
        self.connection.start()
        self.addCleanup(self.connection.stop)

    def test_direct_get_and_post_keep_exact_url(self):
        for data in (None, b'{}'):
            request = urllib.request.Request(self.base + '/model', data=data)
            with transport.open_exact(transport.make_opener(), request, 2) as response:
                self.assertEqual(response.geturl(), request.full_url)
                self.assertEqual(response.read(), b'{"ok":true}')

    def test_redirects_never_reach_another_model(self):
        for code in (301, 302, 303, 307, 308):
            for data in (None, b'{}'):
                with self.subTest(code=code, method='GET' if data is None else 'POST'):
                    request = urllib.request.Request(self.base + '/redirect/' + str(code), data=data)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        transport.open_exact(transport.make_opener(), request, 2)
                    self.assertEqual(error.exception.code, code)
                    error.exception.close()
        self.assertFalse(any(path == '/other-model' for _, path in self.server.paths))

    def test_proxy_environment_does_not_change_endpoint(self):
        with patch.dict('os.environ', {'http_proxy': 'http://127.0.0.1:1', 'no_proxy': ''}, clear=True):
            request = urllib.request.Request(self.base + '/metrics')
            with transport.open_exact(transport.make_opener(), request, 2) as response:
                self.assertEqual(response.status, 200)

    def test_changed_response_url_is_closed_and_refused(self):
        response = Mock()
        response.geturl.return_value = self.base + '/other-model'
        opener = Mock()
        opener.open.return_value = response
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            transport.open_exact(opener, urllib.request.Request(self.base + '/model'), 2)
        response.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
