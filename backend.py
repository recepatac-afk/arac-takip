
import http.server
import socketserver
import json
import os

PORT = 8070
DATA_FILE = "data.json"

# Initialize Data File if missing
if not os.path.exists(DATA_FILE):
    initial_data = {
        "vehicles": [
            { "id": "v1", "plaka": "34ABC123", "driver": "Ahmet Yilmaz", "marka": "Toyota", "model": "Corolla", "yil": 2020, "createdAt": "2025-01-01T00:00:00.000Z" },
            { "id": "v2", "plaka": "06XYZ999", "driver": "Mehmet Demir", "marka": "Honda", "model": "Civic", "yil": 2021, "createdAt": "2025-01-01T00:00:00.000Z" }
        ],
        "expenses": [],
        "appointments": [],
        "settings": {
            "email": "",
            "notifications": True
        }
    }
    with open(DATA_FILE, "w", encoding='utf-8') as f:
        json.dump(initial_data, f)

class MyHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        if self.path == '/api/data':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.wfile.write(b'{}')
        elif self.path == '/':
            self.path = '/index.html'
            super().do_GET()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/data':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                # Validate JSON
                data = json.loads(post_data)
                
                # Create Backup
                if os.path.exists(DATA_FILE):
                    import shutil
                    shutil.copy(DATA_FILE, DATA_FILE + ".bak")

                # Save to file
                with open(DATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "success"}')
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())
        else:
            self.send_response(404)
            self.end_headers()

print(f"Backend running on port {PORT}...")
class ThreadedHTTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

print(f"Backend running on port {PORT}...")
with ThreadedHTTPServer(("", PORT), MyHandler) as httpd:
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
