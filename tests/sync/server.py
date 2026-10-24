"""Tiny HTTP server exposing the real accounts.py data functions with the same routes as main.py, so static/sync.js can be
exercised end-to-end without FastAPI. The caller is identified by the X-Test-User header. Port is passed as argv[1].
Control: POST /__down and /__up make every /api/userdata call fail (simulates an outage)."""
import json, os, sys, tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ["SQLITE_PATH"] = os.path.join(tempfile.mkdtemp(), "sync.db")
import accounts as acc

acc.SIGNUP_CODE = "x"
USERS = {n: acc.create_user(n, "pass-word-1", "x") for n in ("alice", "bob")}
STATE = {"down": False, "calls": []}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _route(self, method):
        if self.path == "/__down": STATE["down"] = True; return self._send(200, {})
        if self.path == "/__up": STATE["down"] = False; return self._send(200, {})
        if self.path == "/__calls": return self._send(200, STATE["calls"])
        user = USERS.get(self.headers.get("X-Test-User", ""))
        if STATE["down"]: return self._send(503, {"detail": "down"})
        if not user: return self._send(401, {"detail": "Login required", "login_required": True})
        STATE["calls"].append(f"{method} {self.path}")
        if self.path == "/api/userdata" and method == "GET":
            return self._send(200, {"user": user, "data": acc.get_all_data(user["id"])})
        if self.path.startswith("/api/userdata/"):
            key = self.path.rsplit("/", 1)[1]
            if method == "DELETE":
                acc.delete_data(user["id"], key); return self._send(200, {"ok": True})
            b = self._body()
            try:
                return self._send(200, {"ok": True, **acc.put_data(user["id"], key, b.get("value"), b.get("base_version"), b.get("force", False))})
            except acc.Conflict as c:
                return self._send(409, {"detail": "conflict", "conflict": True, "current": c.current})
            except acc.AccountError as e:
                return self._send(e.status, {"detail": e.message})
        self._send(404, {})

    def do_GET(self): self._route("GET")
    def do_PUT(self): self._route("PUT")
    def do_POST(self): self._route("POST")
    def do_DELETE(self): self._route("DELETE")


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
