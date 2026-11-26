"""발표·시연용 실행기 — 명령 하나로 '깨끗한 데모 서버'를 켠다.

    python3 demo.py            # http://localhost:9090
    python3 demo.py --port 9191

- 실제 데이터(data/)와 .env 의 키는 건드리지 않는다. 매번 임시 폴더에 새 데이터베이스를 만든다.
- 소셜 로그인 키 없이 '개발용 로그인'으로 일반 사용자·관리자를 바로 바꿔 가며 보여 줄 수 있다.
- '3명 확인' 흐름을 바로 보여 줄 수 있게, 2명이 이미 확인한 제보 하나를 미리 넣어 둔다.
  → 발표자가 세 번째로 '확인'을 누르면 그 자리에서 지도에 공개된다.
- GitHub 자동 보관(DATA_REPO)은 끈다. 데모에서 한 일은 어디에도 올라가지 않는다.
"""
import argparse
import functools
import os
import shutil
import signal
import sys
import tempfile
import webbrowser

print = functools.partial(print, flush=True)   # noqa: A001  (로그가 바로 보이게)

# ── 서버 모듈을 불러오기 전에 환경을 데모용으로 바꾼다 (db·cloudsync 가 import 될 때 값을 읽음) ──
DEMO_DIR = tempfile.mkdtemp(prefix="smoking-demo-")
os.environ["SMOKING_DATA_DIR"] = DEMO_DIR
os.environ["SMOKING_ENV_FILE"] = os.path.join(DEMO_DIR, "no.env")     # 실제 .env 를 읽지 않음
for k in ("DATA_REPO", "DATA_REPO_TOKEN", "NOTIFY_WEBHOOK_URL", "RENDER", "PORT", "PUBLIC_URL"):
    os.environ.pop(k, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db       # noqa: E402
import server   # noqa: E402

DEMO_REPORT = ("[시연] 단국대 정문 버스정류장 뒤 흡연부스", 36.8331, 127.1781)


def seed_demo():
    """2명이 확인한 제보 하나 + 확인한 가짜 사용자 2명."""
    name, lat, lon = DEMO_REPORT
    with db.tx() as c:
        for i in (1, 2):
            c.execute("INSERT OR IGNORE INTO users(id, provider, name, created_at, last_login) VALUES (?,?,?,?,?)",
                      (f"dev:demo{i}", "dev", f"[개발] 시연 이용자{i}", db.now(), db.now()))
        cur = c.execute("""INSERT INTO places(name, lat, lon, address, note, source, status, roof, ashtray, setting,
                           created_by, created_at) VALUES (?,?,?,?,?, 'user', 'pending', 'yes', 'yes', 'outdoor', ?, ?)""",
                        (name, lat, lon, "충남 천안시 동남구 단대로 119 부근", "발표 시연용 제보 (실제 장소 아님)",
                         "dev:demo1", db.now()))
        pid = cur.lastrowid
        for i in (1, 2):
            c.execute("INSERT INTO confirmations VALUES (?,?,?)", (pid, f"dev:demo{i}", db.now()))
    return pid


def main():
    ap = argparse.ArgumentParser(description="천안 흡연장 찾기 — 발표용 데모 서버")
    ap.add_argument("--port", type=int, default=9090)
    ap.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않음")
    args = ap.parse_args()

    try:
        srv, _ = server.create_server("127.0.0.1", args.port, dev_login=True, backups=False)
    except OSError:
        sys.exit(f"포트 {args.port} 이(가) 이미 사용 중입니다. 예: python3 demo.py --port 9191")
    seed_demo()
    with db.tx() as c:
        counts = dict(c.execute("SELECT status, COUNT(*) FROM places GROUP BY status").fetchall())

    base = f"http://localhost:{args.port}"
    line = "─" * 64
    print(f"""
{line}
  천안 흡연장 찾기 · 발표용 데모 서버
{line}
  주소          {base}
  데이터        공개 {counts.get('active', 0)}곳 · 확인 중 {counts.get('pending', 0)}곳 (임시 폴더, 끄면 사라짐)

  바로 로그인 (주소창에 붙여넣기)
    일반 사용자  {base}/auth/dev/login?name=발표자
    관리자       {base}/auth/dev/login?name=관리자&admin=1

  시연 순서 (약 3분)
    1. 첫 화면 노란 표지판 → 검은 띠 '내 위치로 찾기' (또는 지도 클릭)
    2. 가까운 순 목록 · 거리 · 도보 시간 → 하나 눌러 길찾기 / 로드뷰
    3. 일반 사용자로 로그인 → '확인 중' 제보 "{DEMO_REPORT[0]}"
       → '확인' 누르면 3/3 이 되어 바로 지도에 공개됨
    4. 관리자로 로그인 → 👑 관리자 화면 (승인 대기 · 신고 · 사진 · 문의)

  끄기: Ctrl + C
{line}""")
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)        # 창을 닫거나 kill 해도 임시 데이터를 지운다
    if not args.no_browser:
        try:
            webbrowser.open(base)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n데모 서버를 껐습니다. 임시 데이터는 지웠습니다.")
    finally:
        srv.server_close()
        shutil.rmtree(DEMO_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
