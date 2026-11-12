"""Arac Takip sunucusu (v3).

Veri iki yerde olabilir:
  * SUPABASE_URL + SUPABASE_SERVICE_KEY ortam degiskenleri varsa: Supabase (kalici).
  * Yoksa: yerel data.json (yalniz kendi bilgisayarinda denemek icin; Render'da KALICI DEGIL).

Guvenlik: APP_TOKEN ortam degiskeni verilirse /api/* istekleri X-App-Token basligi ister.
Veri kaybini onlemek icin: bos/kucuk veri ezmesi reddedilir, eski surum gecmise yazilir,
surum cakismasi (baska cihaz) 409 ile bildirilir.
Yalniz standart kutuphane kullanir.
"""
import hmac
import http.server
import json
import os
import re
import socketserver
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

PORT = int(os.environ.get("PORT", 8070))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data.json")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
APP_TOKEN = os.environ.get("APP_TOKEN", "")
MAX_BODY = int(os.environ.get("MAX_BODY_MB", "40")) * 1024 * 1024
HISTORY_KEEP = 40
LIST_KEYS = ("vehicles", "expenses", "appointments", "documents")
LOCK = threading.Lock()
STATIC_OK = {"/index.html", "/manifest.json", "/sw.js", "/fatura.html"}


class HttpError(Exception):
    def __init__(self, code, message, extra=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra or {}


# ---------------------------------------------------------------- Supabase
def sb_request(method, path, body=None, prefer=None, query=""):
    url = f"{SUPABASE_URL}/rest/v1/{path}{query}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise HttpError(502, f"Depo hatasi {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise HttpError(502, f"Depoya ulasilamadi: {e.reason}")


class SupabaseStore:
    name = "supabase"

    def load(self):
        rows = sb_request("GET", "app_state", query="?id=eq.1&select=data,version,updated_at")
        if not rows:
            return None, 0
        state, version = rows[0]["data"], int(rows[0]["version"])
        docs = sb_request("GET", "belgeler", query="?select=id,vehicle_id,type,name,doc_date,data&order=updated_at.asc")
        state["documents"] = [
            {"id": d["id"], "vehicleId": d["vehicle_id"], "type": d["type"], "name": d["name"],
             "date": d["doc_date"], "data": d["data"]} for d in (docs or [])
        ]
        return state, version

    def save(self, state, new_version, old_state, old_version):
        docs = state.get("documents", [])
        slim = {k: v for k, v in state.items() if k != "documents"}
        # 1) onceki surumu gecmise al
        if old_state is not None:
            old_slim = {k: v for k, v in old_state.items() if k != "documents"}
            sb_request("POST", "app_state_history", {"version": old_version, "data": old_slim})
            old = sb_request("GET", "app_state_history", query="?select=hid&order=hid.desc&offset=%d&limit=1" % HISTORY_KEEP)
            if old:
                sb_request("DELETE", "app_state_history", query="?hid=lte.%d" % old[0]["hid"])
        # 2) belgeler: yeni olanlari yaz, kaldirilanlari sil
        existing = sb_request("GET", "belgeler", query="?select=id")
        existing_ids = {d["id"] for d in (existing or [])}
        new_ids = {d["id"] for d in docs}
        for d in docs:
            if d["id"] in existing_ids:
                continue
            sb_request("POST", "belgeler", {
                "id": d["id"], "vehicle_id": d.get("vehicleId"), "type": d.get("type"),
                "name": d.get("name"), "doc_date": d.get("date"), "data": d.get("data", "")},
                prefer="resolution=merge-duplicates")
        for gone in existing_ids - new_ids:
            sb_request("DELETE", "belgeler", query="?id=eq." + urllib.parse.quote(gone, safe=""))
        # 3) durum satiri en sonda (belgeler yazilmadan surum ilerlemesin)
        sb_request("POST", "app_state", {"id": 1, "data": slim, "version": new_version},
                   prefer="resolution=merge-duplicates")


# ---------------------------------------------------------------- Yerel dosya
class FileStore:
    name = "dosya"

    def load(self):
        if not os.path.exists(DATA_FILE):
            return None, 0
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state, int(state.get("version", 0))

    def save(self, state, new_version, old_state, old_version):
        state = dict(state)
        state["version"] = new_version
        fd, tmp = tempfile.mkstemp(dir=BASE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        if os.path.exists(DATA_FILE):
            try:
                os.replace(DATA_FILE, DATA_FILE + ".bak")
            except OSError:
                pass
        os.replace(tmp, DATA_FILE)


STORE = SupabaseStore() if (SUPABASE_URL and SUPABASE_KEY) else FileStore()


# ---------------------------------------------------------------- Fatura fotograflari (Supabase Storage, OZEL kova)
BUCKET = "faturalar"
MAX_PHOTO = 6 * 1024 * 1024
FATURA_RE = re.compile(r"^/api/fatura/(liste|(\d{1,9})(/url)?)$")


def sb_storage(method, path, body=None, content_type="application/json"):
    url = f"{SUPABASE_URL}/storage/v1/{path}"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": content_type}
    if method == "POST" and path.startswith("object/" + BUCKET + "/"):
        headers["x-upsert"] = "true"  # ayni kayda yeni foto eskisinin yerine gecer
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise HttpError(502, "Foto deposu hatasi %d: %s" % (e.code, e.read().decode("utf-8", "replace")[:200]))
    except urllib.error.URLError as e:
        raise HttpError(502, "Foto deposuna ulasilamadi: %s" % e.reason)


def fatura_gerekli():
    if not (SUPABASE_URL and SUPABASE_KEY):
        raise HttpError(503, "Fatura fotograflari icin Supabase baglantisi gerekli")


def fatura_liste(hepsi):
    fatura_gerekli()
    q = "?select=id,tarih,plaka,firma,kategori,tutar,belge_adi&order=tarih.desc,id.desc&limit=300"
    if not hepsi:
        q += "&belge_adi=is.null"
    return sb_request("GET", "giderler", query=q) or []


def fatura_kayit(gid):
    rows = sb_request("GET", "giderler", query="?id=eq.%d&select=id,belge_adi" % gid)
    if not rows:
        raise HttpError(404, "Boyle bir gider kaydi yok")
    return rows[0]


def fatura_yukle(gid, foto):
    fatura_gerekli()
    if len(foto) < 1000 or foto[:3] != b"\xff\xd8\xff":
        raise HttpError(400, "Dosya JPEG foto degil")
    fatura_kayit(gid)
    yol = "gider-%d.jpg" % gid
    sb_storage("POST", "object/%s/%s" % (BUCKET, yol), foto, "image/jpeg")
    sb_request("PATCH", "giderler", {"belge_adi": yol}, prefer="return=minimal", query="?id=eq.%d" % gid)
    return {"ok": True, "belge_adi": yol}


def fatura_url(gid):
    fatura_gerekli()
    yol = fatura_kayit(gid).get("belge_adi")
    if not yol:
        raise HttpError(404, "Bu kayda belge eklenmemis")
    r = sb_storage("POST", "object/sign/%s/%s" % (BUCKET, yol), json.dumps({"expiresIn": 300}).encode())
    return {"url": SUPABASE_URL + "/storage/v1" + r["signedURL"]}


# ---------------------------------------------------------------- Kurallar
def validate(data):
    if not isinstance(data, dict):
        raise HttpError(400, "Veri bir nesne olmali")
    for k in LIST_KEYS:
        if k in data and not isinstance(data[k], list):
            raise HttpError(400, f"'{k}' liste olmali")
        data.setdefault(k, [])
    if not isinstance(data.get("settings", {}), dict):
        raise HttpError(400, "'settings' nesne olmali")
    data.setdefault("settings", {})
    for k in ("vehicles", "expenses", "appointments", "documents"):
        for item in data[k]:
            if not isinstance(item, dict) or not item.get("id"):
                raise HttpError(400, f"'{k}' icinde id'si olmayan kayit var")
    for d in data["documents"]:
        if not isinstance(d.get("data", ""), str):
            raise HttpError(400, "Belge verisi metin olmali")


def shrink_guard(old, new):
    """Dolu veriyi bos/cok kucuk veriyle ezmeyi engeller."""
    if not old:
        return
    ov, nv = len(old.get("vehicles", [])), len(new.get("vehicles", []))
    oe, ne = len(old.get("expenses", [])), len(new.get("expenses", []))
    od, nd = len(old.get("documents", [])), len(new.get("documents", []))
    if ov >= 3 and nv <= 2:
        raise HttpError(422, "Veri ezmesi engellendi: arac sayisi %d'den %d'ye dusuyor" % (ov, nv))
    if oe >= 10 and ne < oe * 0.7:
        raise HttpError(422, "Veri ezmesi engellendi: gider sayisi %d'den %d'ye dusuyor" % (oe, ne))
    if od >= 3 and nd < od * 0.5:
        raise HttpError(422, "Veri ezmesi engellendi: belge sayisi %d'den %d'ye dusuyor" % (od, nd))


def token_ok(headers):
    if not APP_TOKEN:
        return True
    given = headers.get("X-App-Token", "")
    return hmac.compare_digest(given.encode("utf-8"), APP_TOKEN.encode("utf-8"))


# ---------------------------------------------------------------- Sunucu
class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "AracTakip/3"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, fmt, *args):
        pass  # kisisel veri iceren adresleri gunluge yazma

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-App-Token")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/health":
            return self._json(200, {"ok": True, "store": STORE.name, "auth": bool(APP_TOKEN)})
        if path == "/api/data":
            if not token_ok(self.headers):
                return self._json(401, {"error": "Parola gerekli"})
            try:
                with LOCK:
                    state, version = STORE.load()
                if state is None:
                    state = {"vehicles": [], "expenses": [], "appointments": [], "documents": [], "settings": {}}
                state["version"] = version
                return self._json(200, state)
            except HttpError as e:
                return self._json(e.code, {"error": e.message})
        m = FATURA_RE.match(path)
        if m:
            if not token_ok(self.headers):
                return self._json(401, {"error": "Parola gerekli"})
            try:
                if m.group(1) == "liste":
                    return self._json(200, fatura_liste("hepsi=1" in self.path))
                if m.group(3):
                    return self._json(200, fatura_url(int(m.group(2))))
            except HttpError as e:
                return self._json(e.code, {"error": e.message})
            return self._json(404, {"error": "bulunamadi"})
        if path == "/":
            self.path = "/index.html"
            path = "/index.html"
        if path not in STATIC_OK:  # data.json, backend.py, .git vb. disariya verilmez
            return self._json(404, {"error": "bulunamadi"})
        return super().do_GET()

    def do_POST(self):
        m = FATURA_RE.match(self.path.split("?")[0])
        if m and m.group(2) and not m.group(3):
            if not token_ok(self.headers):
                return self._json(401, {"error": "Parola gerekli"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_PHOTO:
                    raise HttpError(413, "Foto boyutu gecersiz veya cok buyuk")
                return self._json(200, fatura_yukle(int(m.group(2)), self.rfile.read(length)))
            except HttpError as e:
                return self._json(e.code, {"error": e.message})
            except Exception as e:
                return self._json(500, {"error": "Sunucu hatasi: %s" % type(e).__name__})
        if self.path.split("?")[0] != "/api/data":
            return self._json(404, {"error": "bulunamadi"})
        if not token_ok(self.headers):
            return self._json(401, {"error": "Parola gerekli"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                raise HttpError(413, "Veri boyutu gecersiz veya cok buyuk")
            try:
                data = json.loads(self.rfile.read(length))
            except ValueError:
                raise HttpError(400, "Gecersiz JSON")
            validate(data)
            base = data.pop("baseVersion", None)
            force = self.headers.get("X-Force") == "1"
            with LOCK:
                old, version = STORE.load()
                if base is not None and old is not None and int(base) != version and not force:
                    raise HttpError(409, "Baska bir cihaz daha yeni kaydetti", {"serverVersion": version})
                if not force:
                    shrink_guard(old, data)
                data.pop("version", None)
                STORE.save(data, version + 1, old, version)
            return self._json(200, {"status": "success", "version": version + 1})
        except HttpError as e:
            return self._json(e.code, dict({"error": e.message}, **e.extra))
        except Exception as e:  # beklenmeyen hata: sunucuyu dusurme
            return self._json(500, {"error": "Sunucu hatasi: %s" % type(e).__name__})


class ThreadedHTTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print("Sunucu %d portunda. Depo: %s. Parola: %s" % (PORT, STORE.name, "var" if APP_TOKEN else "YOK"))
    if STORE.name == "dosya" and os.environ.get("RENDER"):
        print("UYARI: Render'da dosya deposu kalici degildir. SUPABASE_URL ve SUPABASE_SERVICE_KEY ekleyin.")
    with ThreadedHTTPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nDuruyor...")
