"""천안시 흡연장 찾기 웹 서버.

실행:
    python3 server.py                       # http://localhost:9090
    python3 server.py --dev-login           # 소셜 로그인 키 없이 테스트 (이 PC에서만)
    python3 server.py --public-url https://도메인 --behind-proxy   # 인터넷 공개 시 (README 참고)

개인정보 원칙: 이용자의 현재 위치는 서버로 보내지 않는다. 화면이 흡연장 목록을 받아 휴대폰 안에서 거리를 계산한다.
서버 로그에는 주소의 ?뒤(검색 조건)를 남기지 않는다.

Python 표준 라이브러리만 사용한다. 자세한 API 목록은 README.md 참고.
"""
import argparse
import collections
import contextlib
import json
import os
import re
import secrets
import threading
import time
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import envfile  # noqa: F401  (.env 를 먼저 읽는다 — db·auth 보다 앞에 있어야 함)
import db
from auth import LABELS, PROVIDERS, Auth
from find_smoking import CHEONAN_LAT, CHEONAN_LON, haversine_m

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
PAGES = {   # 서버가 내보내는 파일은 이 목록뿐 (경로 조작 방지)
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/privacy": ("privacy.html", "text/html; charset=utf-8"),
    "/terms": ("terms.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
DUPLICATE_M = 15          # 이 거리 안이면 같은 장소로 본다
MAX_BODY = 10_000
MAX_PHOTO_BODY = 3_000_000          # 사진 업로드 요청 (base64)
MAX_PHOTO_BYTES = 2_000_000         # 사진 파일 자체
PHOTO_NAME = re.compile(r"^[0-9a-f]{24}\.jpg$")
FEEDBACK_KINDS = {"bug", "data", "idea", "other"}
YES_NO = {"", "yes", "no"}
SETTINGS = {"", "indoor", "outdoor"}
ISSUE_KINDS = {"gone", "moved", "info", "still_here"}
DEFAULT_TILES = {"url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "attribution": "&copy; OpenStreetMap contributors"}


class RateLimiter:
    """key 별로 window 초 안에 limit 번까지만 허용."""

    def __init__(self):
        self.hits = collections.defaultdict(collections.deque)
        self.last_prune = time.time()

    def allow(self, key, limit, window):
        now = time.time()
        if now - self.last_prune > 600:   # 하루 넘게 쓰이지 않은 기록(IP 포함)은 지운다
            self.hits = collections.defaultdict(collections.deque,
                                                {k: q for k, q in self.hits.items() if q and now - q[-1] < 86400})
            self.last_prune = now
        q = self.hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


class Notifier:
    """관리자 알림. config.json 의 notify.webhook_url 이 있을 때만 보낸다 (디스코드·슬랙 웹훅 등).
    개인정보(이름·이메일·좌표)는 보내지 않고 '무슨 일이 생겼는지'만 보낸다."""

    def __init__(self, url):
        self.url = url
        self.last = {}

    def send(self, text, key=None, every=0):
        if not self.url:
            return
        now = time.time()
        if key and now - self.last.get(key, 0) < every:   # 같은 종류 알림이 너무 자주 가지 않게
            return
        if key:
            self.last[key] = now

        def post():
            try:
                body = json.dumps({"text": text, "content": text}).encode()
                req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5).close()
            except Exception as e:
                print(f"[알림 실패] {e}")
        threading.Thread(target=post, daemon=True).start()


def strip_jpeg_metadata(data):
    """JPEG 안의 EXIF(촬영 위치·기기 정보 등)와 주석을 지운다. JPEG가 아니면 None."""
    if data[:3] != b"\xff\xd8\xff":
        return None
    out, i = bytearray(b"\xff\xd8"), 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xDA:                      # 이미지 본문 시작 → 나머지는 그대로
            out += data[i:]
            return bytes(out)
        size = int.from_bytes(data[i + 2:i + 4], "big")
        seg = data[i:i + 2 + size]
        if not (0xE1 <= marker <= 0xEF or marker == 0xFE):   # APP1~APP15(EXIF 등), COM 은 버린다
            out += seg
        i += 2 + size
    return None


class Rules:
    def __init__(self, required=3, gone=3):
        self.required = required   # 자동 공개에 필요한 확인 인원
        self.gone = gone           # 자동 숨김에 필요한 '없어졌어요' 신고 인원


def place_json(r, me=None, favs=(), conf=None, photos=None):
    d = {k: r[k] for k in ("id", "name", "lat", "lon", "address", "note", "source", "status",
                           "roof", "ashtray", "setting", "verified_at")}
    d["favorite"] = r["id"] in favs
    d["photos"] = (photos or {}).get(r["id"], [])
    if r["status"] == "pending" and conf is not None:
        n, mine = conf.get(r["id"], (0, 0))
        d.update(confirms=n, confirmed_by_me=bool(mine), mine=bool(me and r["created_by"] == me["id"]))
    return d


def make_handler(auth, rules, opts, notifier):
    limiter = RateLimiter()

    class Handler(BaseHTTPRequestHandler):
        server_version = "SmokingFinder"
        sys_version = ""

        # ---------- 공통 ----------
        def headers_common(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header("X-Frame-Options", "DENY")

        @contextlib.contextmanager
        def txn(self):
            """DB 작업 중에 만든 응답은 저장(commit)이 끝난 뒤에 보낸다. (먼저 보내면 바로 이어진 요청이 옛 데이터를 볼 수 있다)"""
            self._defer = []
            try:
                with db.tx() as c:
                    yield c
                ready = self._defer
            finally:
                self._defer = None
            for args in ready:
                self.send_json(*args)

        def send_json(self, code, obj, cookie=None):
            if getattr(self, "_defer", None) is not None:
                self._defer.append((code, obj, cookie))
                return
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.headers_common()
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            if self.command != "HEAD":   # HEAD 요청은 헤더만 보낸다
                self.wfile.write(body)

        def fail(self, code, msg, **extra):
            return self.send_json(code, {"ok": False, "error": msg, **extra})

        def redirect(self, to, cookie=None):
            self.send_response(302)
            self.send_header("Location", to)
            self.headers_common()
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()

        def client_ip(self):
            fwd = self.headers.get("X-Forwarded-For")
            return fwd.split(",")[0].strip() if opts.behind_proxy and fwd else self.client_address[0]

        def base_url(self):
            return opts.public_url or f"http://{self.headers.get('Host', 'localhost:9090')}"

        def user(self):
            return auth.user_from_cookie(self.headers.get("Cookie"))

        def read_json(self, limit=MAX_BODY):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length > limit:
                raise ValueError("요청이 너무 큽니다.")
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

        def log_message(self, fmt, *args):
            # 주소의 ? 뒤에는 위치 같은 개인정보가 들어갈 수 있어 지운다
            print(f"[{self.log_date_time_string()}] " + re.sub(r"\?\S*", "", fmt % args))

        def safely(self, fn):
            try:
                fn()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                print("[서버 오류]\n" + traceback.format_exc())
                notifier.send("⚠️ 흡연장 서버에서 오류가 났습니다. 서버 기록을 확인하세요.", key="error", every=600)
                try:
                    self._defer = None
                    self.fail(500, "서버 오류가 났습니다. 잠시 뒤에 다시 시도하세요.")
                except Exception:
                    pass

        def do_GET(self):
            self.safely(self._get)

        def do_HEAD(self):     # Render 등이 상태 확인에 쓰는 요청. GET 과 같지만 본문은 보내지 않는다
            self.safely(self._get)

        def do_POST(self):
            self.safely(self._post)

        # ---------- GET ----------
        def _get(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            parts = url.path.strip("/").split("/")
            if url.path in PAGES:
                return self.send_file(*PAGES[url.path])
            routes = {"/api/places": self.get_places, "/api/status": self.get_status, "/api/config": self.get_config,
                      "/api/me": self.get_me, "/api/admin/queue": self.get_admin_queue, "/api/admin/summary": self.get_admin_summary,
                      "/auth/providers": self.get_providers, "/healthz": self.get_health}
            if url.path in routes:
                return routes[url.path]()
            if len(parts) == 2 and parts[0] == "photos" and PHOTO_NAME.match(parts[1]):
                return self.send_photo(parts[1])
            if len(parts) == 3 and parts[0] == "auth" and parts[1] in PROVIDERS and parts[2] == "login":
                try:
                    return self.redirect(auth.login_url(parts[1], f"{self.base_url()}/auth/{parts[1]}/callback",
                                                        app=q.get("app", ["0"])[0] == "1"))
                except ValueError as e:
                    return self.redirect("/?login_error=" + urllib.parse.quote(str(e)))
            if len(parts) == 3 and parts[0] == "auth" and parts[1] in PROVIDERS and parts[2] == "callback":
                if "error" in q:
                    return self.redirect("/?login_error=" + urllib.parse.quote("로그인을 취소했습니다."))
                try:
                    uid, app = auth.callback(parts[1], q.get("code", [""])[0], q.get("state", [""])[0],
                                             f"{self.base_url()}/auth/{parts[1]}/callback")
                    print(f"[로그인] {uid}" + (" (앱)" if app else ""))
                    if app:
                        return self.send_app_return(auth.issue_app_token(uid))
                    return self.redirect("/", auth.make_cookie(uid))
                except Exception as e:   # 외부 서버 오류 포함
                    print(f"[로그인 실패] {parts[1]}: {e}")
                    return self.redirect("/?login_error=" + urllib.parse.quote("로그인에 실패했습니다. 다시 시도해 주세요."))
            if url.path == "/auth/app-session":
                # 안드로이드 앱이 1회용 토큰으로 로그인 쿠키를 받는다
                uid = auth.consume_app_token(q.get("token", [""])[0])
                if not uid:
                    return self.redirect("/?login_error=" + urllib.parse.quote("로그인 시간이 지났어요. 다시 시도해 주세요."))
                return self.redirect("/", auth.make_cookie(uid))
            if url.path == "/auth/dev/login":
                # 개발용: --dev-login 으로 켰고, 이 PC에서 직접 접속한 경우에만
                if not auth.dev_login or opts.behind_proxy or self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self.fail(404, "없는 주소입니다.")
                name = (q.get("name", ["테스트"])[0] or "테스트")[:20]
                uid = auth.upsert_user("dev", name, f"[개발] {name}", "", role="admin" if q.get("admin", ["0"])[0] == "1" else None)
                return self.redirect("/", auth.make_cookie(uid))
            self.fail(404, "없는 주소입니다.")

        def send_app_return(self, token):
            """휴대폰 브라우저에서 로그인을 마친 뒤 앱으로 돌아가는 화면."""
            t = urllib.parse.quote(token)
            intent = f"intent://login?token={t}#Intent;scheme=smokingfinder;package=kr.cheonan.smokingfinder;end"
            html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>앱으로 돌아가기</title>
<style>body{{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;
background:#ffe600;color:#231815;font-family:sans-serif;text-align:center;padding:24px;box-sizing:border-box}}
a{{display:inline-block;background:#231815;color:#fff;font-weight:700;padding:14px 22px;border-radius:999px;text-decoration:none}}</style>
</head><body><h1 style="margin:0;font-size:1.4rem">로그인 완료</h1><p style="margin:0">흡연장 찾기 앱으로 돌아갑니다.</p>
<a id="back" href="{intent}">앱으로 돌아가기</a>
<script>setTimeout(function(){{location.href=document.getElementById('back').href}},300)</script></body></html>"""
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.headers_common()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_file(self, name, ctype):
            try:
                with open(os.path.join(STATIC, name), "rb") as f:
                    body = f.read()
            except FileNotFoundError:
                return self.fail(404, "없는 주소입니다.")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.headers_common()
            self.end_headers()
            if self.command != "HEAD":   # HEAD 요청은 헤더만 보낸다
                self.wfile.write(body)

        def get_places(self):
            """공개된 흡연장 전체 목록. 위치를 받지 않는다 — 거리 계산은 화면(휴대폰)에서 한다."""
            u = self.user()
            with self.txn() as c:
                sql = "SELECT * FROM places WHERE status = 'active'" + (" OR status = 'pending'" if u else "")
                rows = c.execute(sql).fetchall()
                favs, conf, photos = set(), {}, {}
                for ph in c.execute("SELECT place_id, file FROM photos WHERE status = 'approved' ORDER BY id"):
                    photos.setdefault(ph[0], []).append(f"/photos/{ph[1]}")
                if u:
                    favs = {r[0] for r in c.execute("SELECT place_id FROM favorites WHERE user_id = ?", (u["id"],))}
                    conf = {r[0]: (r[1], r[2]) for r in c.execute(
                        "SELECT place_id, COUNT(*), SUM(user_id = ?) FROM confirmations GROUP BY place_id", (u["id"],))}
            self.send_json(200, {"ok": True, "required": rules.required, "gone_required": rules.gone,
                                 "places": [place_json(r, u, favs, conf, photos) for r in rows]})

        def get_status(self):
            with self.txn() as c:
                row = c.execute("""SELECT
                    SUM(status = 'active' AND source NOT IN ('candidate', 'synthetic')),
                    SUM(status = 'active' AND source = 'user'),
                    SUM(status = 'active' AND source = 'candidate'),
                    SUM(status = 'active' AND source = 'synthetic'),
                    SUM(status = 'pending') FROM places""").fetchone()
            cnt = {k: v or 0 for k, v in zip(("usable", "user", "candidate", "synthetic", "pending"), row)}
            ok = cnt["usable"] + cnt["candidate"] + cnt["synthetic"] > 0
            self.send_json(200, {"ok": ok, "error": None if ok else "아직 등록된 흡연장이 없습니다.",
                                 "source": dict(cnt, required=rules.required)})

        def send_photo(self, name):
            u = self.user()
            with self.txn() as c:
                ph = c.execute("SELECT * FROM photos WHERE file = ?", (name,)).fetchone()
            # 공개(승인)된 사진, 또는 올린 본인·관리자만 볼 수 있다
            if not ph or not (ph["status"] == "approved" or (u and (u["admin"] or u["id"] == ph["user_id"]))):
                return self.fail(404, "없는 사진입니다.")
            try:
                with open(os.path.join(db.PHOTO_DIR, name), "rb") as f:
                    body = f.read()
            except FileNotFoundError:
                return self.fail(404, "없는 사진입니다.")
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400" if ph["status"] == "approved" else "private, no-store")
            self.headers_common()
            self.end_headers()
            if self.command != "HEAD":   # HEAD 요청은 헤더만 보낸다
                self.wfile.write(body)

        def get_health(self):
            """서버 감시용: DB를 읽을 수 있으면 ok."""
            with self.txn() as c:
                n = c.execute("SELECT COUNT(*) FROM places WHERE status = 'active'").fetchone()[0]
            self.send_json(200, {"ok": True, "places": n, "time": db.now()})

        def get_admin_summary(self):
            u = self.user()
            if not (u and u["admin"]):
                return self.fail(403, "관리자만 볼 수 있습니다.")
            with self.txn() as c:
                one = lambda sql: c.execute(sql).fetchone()[0]
                d = {"pending": one("SELECT COUNT(*) FROM places WHERE status = 'pending'"),
                     "issues": one("SELECT COUNT(*) FROM issues WHERE status = 'open'"),
                     "photos": one("SELECT COUNT(*) FROM photos WHERE status = 'pending'"),
                     "feedback": one("SELECT COUNT(*) FROM feedback WHERE status = 'open'")}
            self.send_json(200, {"ok": True, **d, "total": sum(d.values())})

        def get_config(self):
            tiles = {**DEFAULT_TILES, **auth.config.get("tiles", {})}
            self.send_json(200, {"tiles": tiles, "required": rules.required, "gone_required": rules.gone})

        def get_me(self):
            u = self.user()
            self.send_json(200, {"user": {k: u[k] for k in ("id", "name", "provider", "admin", "banned", "created_at")} if u else None})

        def get_providers(self):
            self.send_json(200, {"providers": auth.providers(), "dev": auth.dev_login})

        def get_admin_queue(self):
            u = self.user()
            if not (u and u["admin"]):
                return self.fail(403, "관리자만 볼 수 있습니다.")
            with self.txn() as c:
                pending = [dict(r) for r in c.execute("""
                    SELECT p.id, p.name, p.lat, p.lon, p.note, p.created_at, p.roof, p.ashtray, p.setting,
                           (SELECT COUNT(*) FROM confirmations WHERE place_id = p.id) AS confirms,
                           (SELECT name FROM users WHERE id = p.created_by) AS by_name
                    FROM places p WHERE p.status = 'pending' ORDER BY p.created_at""")]
                issues = [dict(r) for r in c.execute("""
                    SELECT i.id, i.kind, i.memo, i.created_at, p.id AS place_id, p.name, p.lat, p.lon, p.status AS place_status,
                           (SELECT name FROM users WHERE id = i.user_id) AS by_name,
                           (SELECT COUNT(DISTINCT user_id) FROM issues WHERE place_id = p.id AND kind = i.kind AND status = 'open') AS same
                    FROM issues i JOIN places p ON p.id = i.place_id WHERE i.status = 'open' ORDER BY i.created_at DESC""")]
                hidden = [dict(r) for r in c.execute("SELECT id, name, lat, lon, note FROM places WHERE status = 'hidden' ORDER BY id DESC")]
                users = [dict(r) for r in c.execute("""
                    SELECT u.id, u.name, u.provider, u.role, u.banned, u.created_at, u.last_login,
                           (SELECT COUNT(*) FROM places WHERE created_by = u.id) AS reports
                    FROM users u ORDER BY u.last_login DESC LIMIT 200""")]
                log = [dict(r) for r in c.execute("SELECT at, actor, action, target, detail FROM audit ORDER BY id DESC LIMIT 50")]
                photos = [dict(r) for r in c.execute("""
                    SELECT ph.id, ph.file, ph.created_at, p.id AS place_id, p.name,
                           (SELECT name FROM users WHERE id = ph.user_id) AS by_name
                    FROM photos ph JOIN places p ON p.id = ph.place_id WHERE ph.status = 'pending' ORDER BY ph.id""")]
                feedback = [dict(r) for r in c.execute("""
                    SELECT f.id, f.category, f.message, f.contact, f.created_at,
                           (SELECT name FROM users WHERE id = f.user_id) AS by_name
                    FROM feedback f WHERE f.status = 'open' ORDER BY f.id DESC""")]
            for x in users:
                x["provider_label"] = LABELS.get(x["provider"], x["provider"])
                x["admin"] = auth.is_admin({**x, "email": ""})
            self.send_json(200, {"ok": True, "required": rules.required, "pending": pending, "issues": issues,
                                 "hidden": hidden, "users": users, "log": log,
                                 "photos": photos, "feedback": feedback})

        # ---------- POST ----------
        def _post(self):
            path = urlparse(self.path).path
            parts = path.strip("/").split("/")
            origin = self.headers.get("Origin")
            if origin and origin != self.base_url():
                return self.fail(403, "허용되지 않은 요청입니다.")
            if not limiter.allow(("ip", self.client_ip()), 60, 60):
                return self.fail(429, "요청이 너무 많습니다. 잠시 뒤에 다시 시도하세요.")
            try:
                data = self.read_json(MAX_PHOTO_BODY if path.endswith("/photo") else MAX_BODY)
                if not isinstance(data, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as e:
                return self.fail(400, str(e) if str(e) else "요청 형식이 잘못되었습니다.")

            if path == "/auth/logout":
                return self.send_json(200, {"ok": True}, auth.clear_cookie())
            if path == "/auth/local/login":
                try:
                    uid = auth.local_login(str(data.get("id", "")).strip(), str(data.get("password", "")), self.client_ip())
                except PermissionError as e:
                    return self.fail(429, str(e))
                except ValueError as e:
                    print("[개발자 로그인 실패]")
                    return self.fail(401, str(e))
                print(f"[개발자 로그인] {uid}")
                return self.send_json(200, {"ok": True}, auth.make_cookie(uid))

            u = self.user()
            if path == "/api/feedback":
                return self.add_feedback(u, data)
            if not u:
                return self.fail(401, "로그인이 필요합니다.")
            if path == "/api/me/delete":
                return self.delete_account(u)
            if u["banned"]:
                return self.fail(403, "이용이 제한된 계정입니다. 관리자에게 문의하세요.")

            if path == "/api/places":
                return self.add_place(u, data)
            if len(parts) == 4 and parts[:2] == ["api", "places"] and parts[2].isdigit():
                pid, act = int(parts[2]), parts[3]
                if act == "confirm":
                    return self.confirm(u, pid)
                if act == "issue":
                    return self.report_issue(u, pid, data)
                if act == "favorite":
                    return self.toggle_favorite(u, pid)
                if act == "photo":
                    return self.add_photo(u, pid, data)
            if parts[:2] == ["api", "admin"]:
                if not u["admin"]:
                    return self.fail(403, "관리자만 할 수 있습니다.")
                if len(parts) == 4 and parts[2] == "places" and parts[3].isdigit():
                    return self.admin_place(u, int(parts[3]), data)
                if len(parts) == 5 and parts[2] == "issues" and parts[3].isdigit() and parts[4] == "resolve":
                    return self.admin_resolve(u, int(parts[3]), data)
                if len(parts) == 5 and parts[2] == "users" and parts[4] == "ban":
                    return self.admin_ban(u, urllib.parse.unquote(parts[3]), data)
                if len(parts) == 4 and parts[2] == "photos" and parts[3].isdigit():
                    return self.admin_photo(u, int(parts[3]), data)
                if len(parts) == 5 and parts[2] == "feedback" and parts[3].isdigit() and parts[4] == "done":
                    return self.admin_feedback_done(u, int(parts[3]))
            self.fail(404, "없는 주소입니다.")

        # ---------- 제보 / 확인 ----------
        def clean_fields(self, data, need_pos=True):
            name = " ".join(str(data.get("name", "")).split())
            memo = " ".join(str(data.get("memo", "")).split())
            out = {"name": name, "note": memo}
            if not 2 <= len(name) <= 50:
                raise ValueError("이름은 2~50자로 입력하세요.")
            if len(memo) > 200:
                raise ValueError("메모는 200자 이하로 입력하세요.")
            for k, allowed in (("roof", YES_NO), ("ashtray", YES_NO), ("setting", SETTINGS)):
                v = str(data.get(k, ""))
                if v not in allowed:
                    raise ValueError("시설 정보 값이 잘못되었습니다.")
                out[k] = v
            if need_pos or "lat" in data:
                try:
                    lat, lon = float(data["lat"]), float(data["lon"])
                except (KeyError, TypeError, ValueError):
                    raise ValueError("위치를 정해 주세요.")
                if not (CHEONAN_LAT[0] <= lat <= CHEONAN_LAT[1] and CHEONAN_LON[0] <= lon <= CHEONAN_LON[1]):
                    raise ValueError("천안시 범위 밖의 위치입니다.")
                out.update(lat=lat, lon=lon)
            return out

        def add_place(self, u, data):
            try:
                f = self.clean_fields(data)
            except ValueError as e:
                return self.fail(400, str(e))
            if not u["admin"] and not limiter.allow(("add", u["id"]), 10, 86400):
                return self.fail(429, "하루에 등록할 수 있는 개수(10개)를 넘었습니다.")
            with self.txn() as c:
                near = lambda status: sorted(((haversine_m(f["lat"], f["lon"], r["lat"], r["lon"]), r) for r in
                                              c.execute("SELECT * FROM places WHERE status = ?", (status,))), key=lambda x: x[0])
                pend = near("pending")
                if pend and pend[0][0] <= DUPLICATE_M:      # 확인 중인 같은 장소 → 확인으로 합친다
                    return self._confirm(c, u, pend[0][1], merged=True)
                act = [x for x in near("active") if x[1]["source"] != "candidate"]
                if act and act[0][0] <= DUPLICATE_M and not data.get("force"):
                    return self.fail(409, f"{round(act[0][0])}m 거리에 이미 '{act[0][1]['name']}'이(가) 있습니다.", duplicate=True)
                status = "active" if u["admin"] else "pending"
                cur = c.execute("""INSERT INTO places(name, lat, lon, note, source, status, roof, ashtray, setting,
                                   created_by, created_at, verified_at, approved_how) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (f["name"], f["lat"], f["lon"], f["note"], "admin" if u["admin"] else "user", status,
                                 f["roof"], f["ashtray"], f["setting"], u["id"], db.now(),
                                 db.today() if u["admin"] else "", "관리자 등록" if u["admin"] else ""))
                c.execute("INSERT INTO confirmations VALUES (?,?,?)", (cur.lastrowid, u["id"], db.now()))
                db.audit(c, u["id"], "add", cur.lastrowid, f"{f['name']} → {status}")
            print(f"[제보] #{cur.lastrowid} {f['name']} by {u['id']} → {status}")
            if status == "pending":
                notifier.send("📍 새 흡연장 제보가 들어왔습니다. 관리자 화면 → 승인 대기에서 확인하세요.", key="pending", every=300)
            self.send_json(201, {"ok": True, "id": cur.lastrowid, "status": status, "confirms": 1, "required": rules.required})

        def confirm(self, u, pid):
            if not limiter.allow(("act", u["id"]), 30, 3600):
                return self.fail(429, "잠시 뒤에 다시 시도하세요.")
            with self.txn() as c:
                p = c.execute("SELECT * FROM places WHERE id = ? AND status = 'pending'", (pid,)).fetchone()
                if not p:
                    return self.fail(404, "확인 중인 제보가 아닙니다.")
                return self._confirm(c, u, p)

        def _confirm(self, c, u, p, merged=False):
            if c.execute("SELECT 1 FROM confirmations WHERE place_id = ? AND user_id = ?", (p["id"], u["id"])).fetchone():
                return self.fail(409, "이미 확인한 장소입니다.")
            c.execute("INSERT INTO confirmations VALUES (?,?,?)", (p["id"], u["id"], db.now()))
            n = c.execute("SELECT COUNT(*) FROM confirmations WHERE place_id = ?", (p["id"],)).fetchone()[0]
            status = "pending"
            if n >= rules.required:
                status = "active"
                c.execute("UPDATE places SET status = 'active', verified_at = ?, approved_how = ? WHERE id = ?",
                          (db.today(), f"{n}명 확인", p["id"]))
                db.audit(c, "auto", "approve", p["id"], f"{p['name']} ({n}명 확인)")
            db.audit(c, u["id"], "confirm", p["id"], f"{n}/{rules.required}")
            return self.send_json(200, {"ok": True, "merged": merged, "status": status, "name": p["name"],
                                        "confirms": n, "required": rules.required})

        def report_issue(self, u, pid, data):
            kind = str(data.get("kind", ""))
            memo = " ".join(str(data.get("memo", "")).split())[:200]
            if kind not in ISSUE_KINDS:
                return self.fail(400, "신고 종류가 잘못되었습니다.")
            if not limiter.allow(("act", u["id"]), 30, 3600):
                return self.fail(429, "잠시 뒤에 다시 시도하세요.")
            with self.txn() as c:
                p = c.execute("SELECT * FROM places WHERE id = ? AND status = 'active'", (pid,)).fetchone()
                if not p:
                    return self.fail(404, "공개된 흡연장이 아닙니다.")
                if kind == "still_here":
                    if c.execute("""SELECT 1 FROM issues WHERE place_id = ? AND user_id = ? AND kind = 'still_here'
                                    AND created_at >= ?""", (pid, u["id"], db.today())).fetchone():
                        return self.fail(409, "오늘 이미 확인해 주셨어요.")
                    c.execute("INSERT INTO issues(place_id, user_id, kind, memo, status, created_at) VALUES (?,?,?,?, 'done', ?)",
                              (pid, u["id"], kind, memo, db.now()))
                    c.execute("UPDATE places SET verified_at = ? WHERE id = ?", (db.today(), pid))
                    return self.send_json(200, {"ok": True, "verified_at": db.today()})
                if c.execute("SELECT 1 FROM issues WHERE place_id = ? AND user_id = ? AND kind = ? AND status = 'open'",
                             (pid, u["id"], kind)).fetchone():
                    return self.fail(409, "이미 신고한 내용입니다. 관리자가 확인할 때까지 기다려 주세요.")
                c.execute("INSERT INTO issues(place_id, user_id, kind, memo, created_at) VALUES (?,?,?,?,?)",
                          (pid, u["id"], kind, memo, db.now()))
                n = c.execute("SELECT COUNT(DISTINCT user_id) FROM issues WHERE place_id = ? AND kind = ? AND status = 'open'",
                              (pid, kind)).fetchone()[0]
                hidden = False
                if kind == "gone" and n >= rules.gone:
                    c.execute("UPDATE places SET status = 'hidden' WHERE id = ?", (pid,))
                    db.audit(c, "auto", "hide", pid, f"{p['name']} ('없어졌어요' {n}명)")
                    hidden = True
                db.audit(c, u["id"], "issue", pid, kind)
            notifier.send("🚩 흡연장 정보 신고가 들어왔습니다." + (" (신고가 모여 자동으로 숨겼습니다)" if hidden else "")
                          + " 관리자 화면 → 신고에서 확인하세요.", key="issue", every=300)
            self.send_json(200, {"ok": True, "count": n, "hidden": hidden, "gone_required": rules.gone})

        def toggle_favorite(self, u, pid):
            with self.txn() as c:
                if not c.execute("SELECT 1 FROM places WHERE id = ?", (pid,)).fetchone():
                    return self.fail(404, "없는 흡연장입니다.")
                if c.execute("DELETE FROM favorites WHERE user_id = ? AND place_id = ?", (u["id"], pid)).rowcount:
                    fav = False
                else:
                    c.execute("INSERT INTO favorites VALUES (?,?)", (u["id"], pid))
                    fav = True
            self.send_json(200, {"ok": True, "favorite": fav})

        # ---------- 사진 ----------
        def add_photo(self, u, pid, data):
            import base64
            raw = str(data.get("image", ""))
            if raw.startswith("data:"):
                raw = raw.split(",", 1)[-1]
            try:
                img = base64.b64decode(raw, validate=True)
            except (ValueError, TypeError):
                return self.fail(400, "사진을 읽을 수 없습니다.")
            if not 1000 < len(img) <= MAX_PHOTO_BYTES:
                return self.fail(400, "사진 크기는 2MB 이하여야 합니다.")
            img = strip_jpeg_metadata(img)
            if img is None:
                return self.fail(400, "JPEG 사진만 올릴 수 있습니다.")
            if not u["admin"] and not limiter.allow(("photo", u["id"]), 20, 86400):
                return self.fail(429, "하루에 올릴 수 있는 사진(20장)을 넘었습니다.")
            with self.txn() as c:
                if not c.execute("SELECT 1 FROM places WHERE id = ? AND status IN ('active', 'pending')", (pid,)).fetchone():
                    return self.fail(404, "없는 흡연장입니다.")
                if c.execute("SELECT COUNT(*) FROM photos WHERE place_id = ?", (pid,)).fetchone()[0] >= 10:
                    return self.fail(409, "이 흡연장에는 사진이 이미 충분히 있습니다.")
                os.makedirs(db.PHOTO_DIR, exist_ok=True)
                name = secrets.token_hex(12) + ".jpg"
                with open(os.path.join(db.PHOTO_DIR, name), "wb") as f:
                    f.write(img)
                status = "approved" if u["admin"] else "pending"
                c.execute("INSERT INTO photos(place_id, user_id, file, status, created_at) VALUES (?,?,?,?,?)",
                          (pid, u["id"], name, status, db.now()))
                db.audit(c, u["id"], "photo", pid, status)
            if status == "pending":
                notifier.send("📷 새 사진이 올라왔습니다. 관리자 화면 → 사진에서 확인하세요.", key="photo", every=300)
            self.send_json(201, {"ok": True, "status": status})

        def admin_photo(self, u, phid, data):
            action = str(data.get("action", ""))
            with self.txn() as c:
                ph = c.execute("SELECT * FROM photos WHERE id = ?", (phid,)).fetchone()
                if not ph:
                    return self.fail(404, "없는 사진입니다.")
                if action == "approve":
                    c.execute("UPDATE photos SET status = 'approved' WHERE id = ?", (phid,))
                elif action == "delete":
                    c.execute("DELETE FROM photos WHERE id = ?", (phid,))
                    remove_photo_file(ph["file"])
                else:
                    return self.fail(400, "할 수 없는 작업입니다.")
                db.audit(c, u["id"], "photo_" + action, ph["place_id"], "")
            self.send_json(200, {"ok": True})

        # ---------- 문의 ----------
        def add_feedback(self, u, data):
            cat = str(data.get("category", "other"))
            msg = str(data.get("message", "")).strip()
            contact = " ".join(str(data.get("contact", "")).split())[:100]
            if cat not in FEEDBACK_KINDS:
                cat = "other"
            if not 5 <= len(msg) <= 1000:
                return self.fail(400, "내용을 5~1000자로 적어 주세요.")
            if not limiter.allow(("feedback", self.client_ip()), 5, 3600):
                return self.fail(429, "문의는 1시간에 5번까지 보낼 수 있습니다.")
            with self.txn() as c:
                c.execute("INSERT INTO feedback(user_id, category, message, contact, created_at) VALUES (?,?,?,?,?)",
                          (u["id"] if u else None, cat, msg, contact, db.now()))
            notifier.send("✉️ 새 문의가 들어왔습니다. 관리자 화면 → 문의에서 확인하세요.", key="feedback", every=300)
            self.send_json(201, {"ok": True})

        def admin_feedback_done(self, u, fid):
            with self.txn() as c:
                if not c.execute("UPDATE feedback SET status = 'done' WHERE id = ? AND status = 'open'", (fid,)).rowcount:
                    return self.fail(404, "처리할 문의가 없습니다.")
                db.audit(c, u["id"], "feedback_done", fid, "")
            self.send_json(200, {"ok": True})

        # ---------- 회원 탈퇴 ----------
        def delete_account(self, u):
            gone = "deleted:" + secrets.token_hex(6)   # 확인 인원 수는 유지하되 누구였는지는 지운다
            with self.txn() as c:
                c.execute("UPDATE places SET created_by = ? WHERE created_by = ?", (gone, u["id"]))
                c.execute("UPDATE confirmations SET user_id = ? WHERE user_id = ?", (gone, u["id"]))
                c.execute("UPDATE issues SET user_id = ? WHERE user_id = ?", (gone, u["id"]))
                c.execute("UPDATE issues SET resolved_by = ? WHERE resolved_by = ?", (gone, u["id"]))
                c.execute("UPDATE audit SET actor = ? WHERE actor = ?", (gone, u["id"]))
                c.execute("UPDATE photos SET user_id = ? WHERE user_id = ?", (gone, u["id"]))
                c.execute("UPDATE feedback SET user_id = ?, contact = '' WHERE user_id = ?", (gone, u["id"]))
                c.execute("DELETE FROM favorites WHERE user_id = ?", (u["id"],))
                c.execute("DELETE FROM users WHERE id = ?", (u["id"],))
                db.audit(c, gone, "account_delete", "-", "회원 탈퇴")
            print("[회원 탈퇴] 1명")
            self.send_json(200, {"ok": True}, auth.clear_cookie())

        # ---------- 관리자 ----------
        def admin_place(self, u, pid, data):
            action = str(data.get("action", ""))
            with self.txn() as c:
                p = c.execute("SELECT * FROM places WHERE id = ?", (pid,)).fetchone()
                if not p:
                    return self.fail(404, "없는 흡연장입니다.")
                if action == "approve" and p["status"] == "pending":
                    c.execute("UPDATE places SET status = 'active', verified_at = ?, approved_how = '관리자 승인' WHERE id = ?",
                              (db.today(), pid))
                elif action == "reject" and p["status"] == "pending":
                    c.execute("UPDATE places SET status = 'rejected' WHERE id = ?", (pid,))
                elif action == "hide" and p["status"] == "active":
                    c.execute("UPDATE places SET status = 'hidden' WHERE id = ?", (pid,))
                elif action == "restore" and p["status"] in ("hidden", "rejected"):
                    c.execute("UPDATE places SET status = 'active' WHERE id = ?", (pid,))
                    c.execute("UPDATE issues SET status = 'resolved', resolved_by = ?, resolved_at = ? WHERE place_id = ? AND status = 'open'",
                              (u["id"], db.now(), pid))
                elif action == "delete":
                    for (fname,) in c.execute("SELECT file FROM photos WHERE place_id = ?", (pid,)).fetchall():
                        remove_photo_file(fname)
                    c.execute("DELETE FROM places WHERE id = ?", (pid,))
                elif action == "edit":
                    try:
                        f = self.clean_fields(data, need_pos=False)
                    except ValueError as e:
                        return self.fail(400, str(e))
                    sets = ", ".join(f"{k} = ?" for k in f)
                    c.execute(f"UPDATE places SET {sets}, verified_at = ? WHERE id = ?", (*f.values(), db.today(), pid))
                else:
                    return self.fail(400, "할 수 없는 작업입니다.")
                db.audit(c, u["id"], action, pid, p["name"])
            print(f"[관리] {action} #{pid} by {u['id']}")
            self.send_json(200, {"ok": True})

        def admin_resolve(self, u, iid, data):
            with self.txn() as c:
                i = c.execute("SELECT * FROM issues WHERE id = ? AND status = 'open'", (iid,)).fetchone()
                if not i:
                    return self.fail(404, "처리할 신고가 없습니다.")
                c.execute("UPDATE issues SET status = 'resolved', resolved_by = ?, resolved_at = ? WHERE place_id = ? AND kind = ? AND status = 'open'",
                          (u["id"], db.now(), i["place_id"], i["kind"]))
                if data.get("hide"):
                    c.execute("UPDATE places SET status = 'hidden' WHERE id = ?", (i["place_id"],))
                db.audit(c, u["id"], "resolve" + ("+hide" if data.get("hide") else ""), i["place_id"], i["kind"])
            self.send_json(200, {"ok": True})

        def admin_ban(self, u, uid, data):
            banned = bool(data.get("banned"))
            with self.txn() as c:
                t = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
                if not t:
                    return self.fail(404, "없는 회원입니다.")
                if uid == u["id"] or auth.is_admin(dict(t)):
                    return self.fail(400, "관리자 계정은 제한할 수 없습니다.")
                c.execute("UPDATE users SET banned = ? WHERE id = ?", (int(banned), uid))
                db.audit(c, u["id"], "ban" if banned else "unban", uid, t["name"])
            self.send_json(200, {"ok": True, "banned": banned})

    return Handler


def remove_photo_file(name):
    if PHOTO_NAME.match(name or ""):
        try:
            os.remove(os.path.join(db.PHOTO_DIR, name))
        except FileNotFoundError:
            pass


def create_server(host="127.0.0.1", port=9090, public_url="", behind_proxy=False, dev_login=False,
                  required=3, gone=3, backups=True):
    db.init()
    if backups:
        db.start_daily_backup()
    opts = argparse.Namespace(public_url=public_url.rstrip("/"), behind_proxy=behind_proxy)
    auth = Auth(dev_login=dev_login, secure_cookie=public_url.startswith("https://"))
    notifier = Notifier(auth.config.get("notify", {}).get("webhook_url", ""))
    return ThreadingHTTPServer((host, port), make_handler(auth, Rules(required, gone), opts, notifier)), auth


def main():
    ap = argparse.ArgumentParser(description="천안 흡연장 찾기 웹 서버")
    on_render = bool(os.environ.get("RENDER"))          # Render 가 자동으로 넣어 주는 값
    ap.add_argument("--host", default="0.0.0.0" if os.environ.get("PORT") else "127.0.0.1", help="공개 시에도 프록시 뒤라면 127.0.0.1 그대로")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 9090)))
    ap.add_argument("--public-url", default=os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL", ""), help="인터넷 공개 주소 (예: https://smoke.example.com)")
    ap.add_argument("--behind-proxy", action="store_true", default=on_render, help="nginx/Caddy 뒤에서 실행할 때")
    ap.add_argument("--required", type=int, default=3, help="자동 공개에 필요한 확인 인원 (기본 3)")
    ap.add_argument("--gone", type=int, default=3, help="자동 숨김에 필요한 '없어졌어요' 신고 인원 (기본 3)")
    ap.add_argument("--dev-login", action="store_true", help="소셜 로그인 없이 테스트하는 개발용 로그인 (이 PC에서만)")
    args = ap.parse_args()

    server, auth = create_server(args.host, args.port, args.public_url, args.behind_proxy, args.dev_login,
                                 args.required, args.gone)
    print("로그인: " + ", ".join(f"{p['label']}={'사용' if p['enabled'] else '키 없음'}" for p in auth.providers())
          + " / 개발자 로그인 사용" + (" / 개발용 로그인 켜짐" if args.dev_login else ""))
    print(f"{args.public_url or f'http://localhost:{args.port}'} 에서 실행 중 (Ctrl+C로 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
