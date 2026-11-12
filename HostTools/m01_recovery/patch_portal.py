"""Keep an incomplete TLS handshake out of the portal accept loop."""
import ast
from pathlib import Path

SERVER = '''class BMI30PortalHTTPServer(ThreadingHTTPServer):
    """Accept clients promptly; perform TLS negotiation in each client worker."""
    request_queue_size = 128
    client_timeout = 15.0
    tls_context = None

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(self.client_timeout)
        return request, address

    def process_request_thread(self, request, client_address):
        if self.tls_context is not None:
            try:
                request = self.tls_context.wrap_socket(
                    request, server_side=True, do_handshake_on_connect=False)
                request.do_handshake()
            except (OSError, ValueError):
                self.shutdown_request(request)
                return
        super().process_request_thread(request, client_address)


'''


def patch(source):
    if 'class BMI30PortalHTTPServer(' in source:
        raise ValueError('Portal is already patched')
    replacements = {
        'def main() -> None:\n': SERVER + 'def main() -> None:\n',
        'http_server = ThreadingHTTPServer(("0.0.0.0", PORT), HotspotInfoHandler)':
        'http_server = BMI30PortalHTTPServer(("0.0.0.0", PORT), HotspotInfoHandler)',
        'https_server = ThreadingHTTPServer(("0.0.0.0", HTTPS_PORT), HotspotInfoHandler)':
        'https_server = BMI30PortalHTTPServer(("0.0.0.0", HTTPS_PORT), HotspotInfoHandler)',
        'https_server.socket = ssl_ctx.wrap_socket(https_server.socket, server_side=True)':
        'https_server.tls_context = ssl_ctx',
    }
    for old, new in replacements.items():
        if source.count(old) != 1:
            raise ValueError('Unexpected portal structure: ' + old)
        source = source.replace(old, new)
    ast.parse(source)
    return source


if __name__ == '__main__':
    import sys
    Path(sys.argv[2]).write_text(patch(Path(sys.argv[1]).read_text(encoding='utf-8')), encoding='utf-8')
