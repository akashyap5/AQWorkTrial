"""Notes API: load an installed release once, then serve that snapshot."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sys

snapshot = json.loads(Path(sys.argv[1]).read_text())
release = json.dumps(snapshot['release'], sort_keys=True).encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            body, status = b'{"status":"ok"}', 200
        elif self.path == '/api/release':
            body, status = release, 200
        else:
            body, status = b'{"error":"not found"}', 404
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server(HTTPServer):
    allow_reuse_address = True


server = Server(('127.0.0.1', snapshot['port']), Handler)
Path('/app/run/notes-api-boot.json').write_text(json.dumps({
    'pid': os.getpid(), 'port': snapshot['port'],
}))
server.serve_forever()
