# webapp_server.py
from http.server import HTTPServer, SimpleHTTPRequestHandler
import os

PORT = int(os.environ.get('PORT', 8000))

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory='bingo-game', **kwargs)

print(f"Starting server on port {PORT}")
httpd = HTTPServer(('0.0.0.0', PORT), Handler)
httpd.serve_forever()
