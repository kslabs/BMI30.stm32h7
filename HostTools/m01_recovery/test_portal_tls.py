"""Run on M01 with its existing TLS certificate, using loopback ephemeral ports."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import ssl
import threading
import time
import unittest
from patch_portal import SERVER

exec(SERVER)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'OK')

    def log_message(self, *args):
        pass


class PortalTLS(unittest.TestCase):
    def start(self, legacy=False, tls=True):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain('/etc/ssl/bmi30/portal.crt', '/etc/ssl/bmi30/portal.key')
        server = (ThreadingHTTPServer if legacy else BMI30PortalHTTPServer)(('127.0.0.1', 0), Handler)
        server.client_timeout = 0.7
        if tls:
            if legacy:
                server.socket = ctx.wrap_socket(server.socket, server_side=True)
            else:
                server.tls_context = ctx
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address

    def get(self, address):
        conn = http.client.HTTPSConnection(*address, context=ssl._create_unverified_context(), timeout=0.3)
        try:
            conn.request('GET', '/')
            response = conn.getresponse()
            self.assertEqual((response.status, response.read()), (200, b'OK'))
        finally:
            conn.close()

    def test_original_blocks_second_client(self):
        address = self.start(legacy=True)
        with socket.create_connection(address) as stalled:
            time.sleep(0.05)
            with self.assertRaises((TimeoutError, socket.timeout)):
                self.get(address)

    def test_fixed_serves_while_handshake_stalled(self):
        address = self.start()
        with socket.create_connection(address) as stalled:
            time.sleep(0.05)
            self.get(address)

    def test_stalled_tls_is_closed(self):
        address = self.start()
        with socket.create_connection(address, timeout=2) as stalled:
            self.assertEqual(stalled.recv(1), b'')
        self.get(address)

    def test_incomplete_http_is_closed(self):
        address = self.start(tls=False)
        with socket.create_connection(address, timeout=2) as stalled:
            stalled.sendall(b'GET / HTTP/1.1\r\n')
            self.assertEqual(stalled.recv(1), b'')

    def test_bad_tls_does_not_stop_server(self):
        address = self.start()
        with socket.create_connection(address) as malformed:
            malformed.sendall(b'plain text on TLS port\r\n')
        self.get(address)


if __name__ == '__main__':
    unittest.main(verbosity=2)
