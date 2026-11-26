"""서버 전체 동작 테스트. 실제 data/ 는 건드리지 않고 임시 폴더에서 실행한다.

    cd ~/smoking_finder && python3 -m unittest -v tests.test_api
"""
import contextlib
import http.cookiejar
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

TMP = tempfile.mkdtemp(prefix="smoking-test-")
os.environ["SMOKING_DATA_DIR"] = TMP
os.environ["SMOKING_CONFIG"] = os.path.join(TMP, "no-config.json")
os.environ["SMOKING_ENV_FILE"] = os.path.join(TMP, "no.env")      # 실제 .env 를 읽지 않게
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

with open(os.path.join(TMP, "smoking_areas.csv"), "w", encoding="utf-8") as f:
    f.write("이름,위도,경도,주소,확인일,출처\n가부스,36.8350,127.1660,,2025-02,테스트\n나부스,36.8380,127.1700,,2025-02,테스트\n")

import auth  # noqa: E402
import server  # noqa: E402


class Client:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with self.opener.open(req) as r:
                raw, code = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, code = e.read(), e.code
        try:
            return code, json.loads(raw)
        except ValueError:
            return code, raw.decode("utf-8", "replace")

    def dev_login(self, name, admin=False):
        self.call(f"/auth/dev/login?name={urllib.parse.quote(name)}" + ("&admin=1" if admin else ""))
        return self


def place(c, name):
    return next(p for p in c.call("/api/places")[1]["places"] if p["name"] == name)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.auth = server.create_server(port=0, dev_login=True, backups=False)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.anon = Client(cls.base)
        cls.boss = Client(cls.base).dev_login("boss", admin=True)
        cls.u = [Client(cls.base).dev_login(f"u{i}") for i in range(1, 5)]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)

    def test_01_migration_and_public_list(self):
        self.assertTrue(os.path.exists(os.path.join(TMP, "legacy_files", "smoking_areas.csv")))
        code, d = self.anon.call("/api/places")
        self.assertEqual(code, 200)
        self.assertEqual({p["name"] for p in d["places"]} >= {"가부스", "나부스"}, True)
        self.assertNotIn("created_by", d["places"][0])   # 누가 올렸는지는 공개하지 않는다

    def test_01b_korean_dev_login(self):
        # 앱의 '개발용 로그인' 버튼은 한글 이름(테스터123)을 쓴다 → 쿠키가 만들어져야 한다
        c = Client(self.base).dev_login("테스터123")
        code, d = c.call("/api/me")
        self.assertEqual(code, 200)
        self.assertIn("테스터123", d["user"]["name"])

    def test_02_login_required_and_origin(self):
        self.assertEqual(self.anon.call("/api/places", {"name": "x장소", "lat": 36.836, "lon": 127.169})[0], 401)
        code, _ = self.u[0].call("/api/places", {"name": "x장소", "lat": 36.836, "lon": 127.169}, {"Origin": "http://evil.example"})
        self.assertEqual(code, 403)

    def test_03_three_confirmations_publish(self):
        code, d = self.u[0].call("/api/places", {"name": "다부스", "lat": 36.8400, "lon": 127.1720, "roof": "yes"})
        self.assertEqual((code, d["status"]), (201, "pending"))
        self.assertFalse(any(p["name"] == "다부스" for p in self.anon.call("/api/places")[1]["places"]))  # 비로그인에겐 안 보임
        self.assertEqual(place(self.u[0], "다부스")["confirms"], 1)
        self.assertEqual(self.u[0].call(f"/api/places/{place(self.u[0], '다부스')['id']}/confirm", {})[0], 409)  # 본인 중복
        code, d = self.u[1].call("/api/places", {"name": "다부스2", "lat": 36.84003, "lon": 127.17203})        # 근처 → 합치기
        self.assertTrue(d["merged"])
        pid = place(self.u[2], "다부스")["id"]
        code, d = self.u[2].call(f"/api/places/{pid}/confirm", {})
        self.assertEqual(d["status"], "active")
        p = place(self.anon, "다부스")
        self.assertEqual((p["status"], p["roof"]), ("active", "yes"))

    def test_04_duplicate_and_validation(self):
        code, d = self.u[0].call("/api/places", {"name": "가부스 또", "lat": 36.83501, "lon": 127.16601})
        self.assertEqual(code, 409)
        self.assertEqual(self.u[0].call("/api/places", {"name": "a", "lat": 36.83, "lon": 127.16})[0], 400)
        self.assertEqual(self.u[0].call("/api/places", {"name": "서울", "lat": 37.56, "lon": 126.97})[0], 400)
        self.assertEqual(self.u[0].call("/api/places", {"name": "시설", "lat": 36.83, "lon": 127.16, "roof": "maybe"})[0], 400)

    def test_05_still_here_and_gone_reports(self):
        pid = place(self.anon, "나부스")["id"]
        code, d = self.u[0].call(f"/api/places/{pid}/issue", {"kind": "still_here"})
        self.assertEqual(code, 200)
        self.assertEqual(place(self.anon, "나부스")["verified_at"], d["verified_at"])
        self.assertEqual(self.u[0].call(f"/api/places/{pid}/issue", {"kind": "still_here"})[0], 409)
        for i in range(3):
            code, d = self.u[i].call(f"/api/places/{pid}/issue", {"kind": "gone", "memo": "철거됨"})
            self.assertEqual(code, 200)
        self.assertTrue(d["hidden"])
        self.assertFalse(any(p["id"] == pid for p in self.anon.call("/api/places")[1]["places"]))
        q = self.boss.call("/api/admin/queue")[1]
        self.assertTrue(any(i["place_id"] == pid and i["same"] == 3 for i in q["issues"]))
        issue_id = next(i["id"] for i in q["issues"] if i["place_id"] == pid)
        self.assertEqual(self.boss.call(f"/api/admin/issues/{issue_id}/resolve", {"hide": False})[0], 200)
        self.assertEqual(self.boss.call(f"/api/admin/places/{pid}", {"action": "restore"})[0], 200)
        self.assertTrue(any(p["id"] == pid for p in self.anon.call("/api/places")[1]["places"]))

    def test_06_admin_actions(self):
        self.assertEqual(self.u[0].call("/api/admin/queue")[0], 403)
        code, d = self.boss.call("/api/places", {"name": "관리자부스", "lat": 36.8420, "lon": 127.1740})
        self.assertEqual(d["status"], "active")
        pid = d["id"]
        self.assertEqual(self.u[0].call(f"/api/admin/places/{pid}", {"action": "hide"})[0], 403)
        self.assertEqual(self.boss.call(f"/api/admin/places/{pid}", {"action": "edit", "name": "관리자부스(수정)", "setting": "indoor"})[0], 200)
        self.assertEqual(place(self.anon, "관리자부스(수정)")["setting"], "indoor")
        self.assertEqual(self.boss.call(f"/api/admin/places/{pid}", {"action": "delete"})[0], 200)
        code, d = self.u[3].call("/api/places", {"name": "거절될곳", "lat": 36.8300, "lon": 127.1600})
        self.assertEqual(self.boss.call(f"/api/admin/places/{d['id']}", {"action": "reject"})[0], 200)
        log = self.boss.call("/api/admin/queue")[1]["log"]
        self.assertTrue(any(l["action"] == "reject" for l in log))

    def test_07_ban(self):
        uid = "dev:u4"
        self.assertEqual(self.boss.call(f"/api/admin/users/{urllib.parse.quote(uid)}/ban", {"banned": True})[0], 200)
        self.assertEqual(self.u[3].call("/api/places", {"name": "차단된제보", "lat": 36.8310, "lon": 127.1610})[0], 403)
        self.assertEqual(self.boss.call("/api/admin/users/dev%3Aboss/ban", {"banned": True})[0], 400)  # 관리자는 제한 불가
        self.boss.call(f"/api/admin/users/{urllib.parse.quote(uid)}/ban", {"banned": False})

    def test_08_favorites(self):
        pid = place(self.anon, "가부스")["id"]
        self.assertTrue(self.u[1].call(f"/api/places/{pid}/favorite", {})[1]["favorite"])
        self.assertTrue(place(self.u[1], "가부스")["favorite"])
        self.assertFalse(place(self.u[2], "가부스")["favorite"])
        self.assertFalse(self.u[1].call(f"/api/places/{pid}/favorite", {})[1]["favorite"])

    def test_09_rate_limit(self):
        c = Client(self.base).dev_login("spammer")
        codes = [c.call("/api/places", {"name": f"스팸{i}", "lat": 36.80 + i * 0.002, "lon": 127.10})[0] for i in range(11)]
        self.assertEqual(codes[:10], [201] * 10)
        self.assertEqual(codes[10], 429)

    def test_10_logs_have_no_query(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.anon.call("/api/places?lat=36.8123&lon=127.1456")
        self.assertIn("/api/places", buf.getvalue())
        self.assertNotIn("36.8123", buf.getvalue())

    def test_11_static_pages_and_traversal(self):
        for path in ("/", "/privacy", "/terms", "/manifest.webmanifest", "/sw.js", "/icon.svg"):
            with urllib.request.urlopen(self.base + path) as r:
                self.assertEqual(r.status, 200, path)
        self.assertEqual(self.anon.call("/../server.py")[0], 404)
        self.assertEqual(self.anon.call("/static/../server.py")[0], 404)

    def test_12_local_login(self):
        auth.set_local_account("tester", "pass1234!", role="admin")
        c = Client(self.base)
        self.assertEqual(c.call("/auth/local/login", {"id": "tester", "password": "wrong"})[0], 401)
        self.assertEqual(c.call("/auth/local/login", {"id": "tester", "password": "pass1234!"})[0], 200)
        self.assertTrue(c.call("/api/me")[1]["user"]["admin"])

    def test_13_photos(self):
        import base64
        seg = lambda m, payload: b"\xff" + bytes([m]) + (len(payload) + 2).to_bytes(2, "big") + payload
        exif = b"Exif\x00\x00GPSLatitude=36.8350;GPSLongitude=127.1660"
        jpeg = (b"\xff\xd8" + seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00") + seg(0xE1, exif)
                + seg(0xFE, b"comment") + seg(0xDB, b"\x00" + bytes(64)) + b"\xff\xda" + bytes(1500) + b"\xff\xd9")
        img = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        pid = place(self.anon, "가부스")["id"]
        self.assertEqual(self.anon.call(f"/api/places/{pid}/photo", {"image": img})[0], 401)
        self.assertEqual(self.u[0].call(f"/api/places/{pid}/photo", {"image": base64.b64encode(b"x" * 2000).decode()})[0], 400)
        code, d = self.u[0].call(f"/api/places/{pid}/photo", {"image": img})
        self.assertEqual((code, d["status"]), (201, "pending"))
        self.assertEqual(place(self.anon, "가부스")["photos"], [])          # 검토 전에는 공개 안 됨
        q = self.boss.call("/api/admin/queue")[1]
        ph = next(x for x in q["photos"] if x["place_id"] == pid)
        self.assertEqual(self.anon.call(f"/photos/{ph['file']}")[0], 404)   # 다른 사람은 볼 수 없음
        saved = __import__("pathlib").Path(TMP, "photos", ph["file"]).read_bytes()
        self.assertNotIn(b"GPSLatitude", saved)                             # EXIF(위치) 제거됨
        self.assertNotIn(b"comment", saved)
        self.assertTrue(saved.startswith(b"\xff\xd8") and b"\xff\xda" in saved)
        self.assertEqual(self.boss.call(f"/api/admin/photos/{ph['id']}", {"action": "approve"})[0], 200)
        urls = place(self.anon, "가부스")["photos"]
        self.assertEqual(urls, [f"/photos/{ph['file']}"])
        with urllib.request.urlopen(self.base + urls[0]) as r:
            self.assertEqual(r.headers["Content-Type"], "image/jpeg")
        self.assertEqual(self.anon.call("/photos/../../server.py")[0], 404)

    def test_14_feedback_summary_health(self):
        self.assertEqual(self.anon.call("/api/feedback", {"category": "bug", "message": "짧"})[0], 400)
        self.assertEqual(self.anon.call("/api/feedback", {"category": "bug", "message": "지도가 안 떠요", "contact": "a@b.c"})[0], 201)
        s = self.boss.call("/api/admin/summary")[1]
        self.assertGreaterEqual(s["feedback"], 1)
        self.assertEqual(self.u[0].call("/api/admin/summary")[0], 403)
        fb = self.boss.call("/api/admin/queue")[1]["feedback"][0]
        self.assertEqual(fb["contact"], "a@b.c")
        self.assertEqual(self.boss.call(f"/api/admin/feedback/{fb['id']}/done", {})[0], 200)
        code, h = self.anon.call("/healthz")
        self.assertEqual((code, h["ok"]), (200, True))

    def test_15_webhook_has_no_personal_info(self):
        import http.server
        got = []

        class Hook(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(204); self.end_headers()

            def log_message(self, *a):
                pass
        hook = http.server.HTTPServer(("127.0.0.1", 0), Hook)
        threading.Thread(target=hook.handle_request, daemon=True).start()
        n = server.Notifier(f"http://127.0.0.1:{hook.server_address[1]}/")
        n.send("📍 새 흡연장 제보가 들어왔습니다.")
        for _ in range(50):
            if got:
                break
            __import__("time").sleep(0.05)
        self.assertEqual(got[0]["text"], "📍 새 흡연장 제보가 들어왔습니다.")
        hook.server_close()

    def test_16_head_requests(self):
        for path in ("/", "/healthz"):
            req = urllib.request.Request(self.base + path, method="HEAD")
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status, 200, path)
                self.assertEqual(r.read(), b"", path)          # HEAD 는 본문이 없어야 함

    def test_17_app_login_handoff(self):
        uid = self.auth.upsert_user("dev", "appuser", "[개발] appuser", "")
        token = self.auth.issue_app_token(uid)
        c = Client(self.base)
        c.call("/auth/app-session?token=" + urllib.parse.quote(token))
        self.assertEqual(c.call("/api/me")[1]["user"]["id"], uid)          # 토큰으로 로그인됨
        c2 = Client(self.base)
        c2.call("/auth/app-session?token=" + urllib.parse.quote(token))
        self.assertIsNone(c2.call("/api/me")[1]["user"])                    # 같은 토큰은 두 번 못 씀
        self.assertIsNone(self.auth.consume_app_token("wrong"))

    def test_18_app_login_state(self):
        self.auth.config["google"] = {"client_id": "1-a.apps.googleusercontent.com", "client_secret": "GOCSPX-test"}
        try:
            url = self.auth.login_url("google", "http://x/cb", app=True)
            state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
            self.assertEqual(self.auth.states[state][2], True)              # 앱에서 시작한 로그인으로 기록됨
            url2 = self.auth.login_url("google", "http://x/cb")
            state2 = urllib.parse.parse_qs(urllib.parse.urlparse(url2).query)["state"][0]
            self.assertEqual(self.auth.states[state2][2], False)
        finally:
            self.auth.config.pop("google", None)

    def test_99_account_delete(self):
        c = Client(self.base).dev_login("leaver")
        code, d = c.call("/api/places", {"name": "탈퇴자제보", "lat": 36.8450, "lon": 127.1760})
        self.assertEqual(c.call("/api/me/delete", {})[0], 200)
        self.assertIsNone(c.call("/api/me")[1]["user"])
        q = self.boss.call("/api/admin/queue")[1]
        p = next(x for x in q["pending"] if x["name"] == "탈퇴자제보")
        self.assertEqual((p["confirms"], p["by_name"]), (1, None))   # 확인 수는 남고 작성자는 지워짐
        self.assertFalse(any(u["id"] == "dev:leaver" for u in q["users"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
