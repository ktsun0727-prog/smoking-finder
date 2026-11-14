"""관리 명령.

    python3 manage.py backup                                  # 지금 바로 백업 (data/backups/)
    python3 manage.py export 파일.csv                          # 공개 중인 흡연장을 CSV로 내보내기
    python3 manage.py import 파일.csv --source admin           # CSV를 흡연장으로 가져오기 (바로 공개)
    python3 manage.py import 파일.csv --source synthetic       # 가상 데이터 가져오기 (화면에 '가상' 표시)
    python3 manage.py stats                                   # 현황 숫자
    python3 manage.py passwd 아이디                            # 개발자(관리자) 비밀번호 바꾸기
    python3 manage.py hash-password                           # Render 환경변수 ADMIN_PASSWORD_HASH 에 넣을 값 만들기
    python3 manage.py make-env                                # 지금 설정(config.json·secret.key·관리자 계정)을 .env 하나로 모으기
"""
import argparse
import csv
import getpass
import os
import sys

import envfile  # noqa: F401  (.env 를 먼저 읽는다)
import db
from auth import CONFIG_PATH, LOCAL_PATH, SECRET_PATH, load_json, password_hash_string, set_local_account


def main():
    ap = argparse.ArgumentParser(description="천안 흡연장 찾기 관리 명령")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backup")
    sub.add_parser("stats")
    e = sub.add_parser("export"); e.add_argument("path")
    i = sub.add_parser("import"); i.add_argument("path"); i.add_argument("--source", choices=["admin", "user", "candidate", "synthetic"], required=True)
    p = sub.add_parser("passwd"); p.add_argument("login_id")
    sub.add_parser("hash-password")
    sub.add_parser("make-env")
    a = ap.parse_args()

    if a.cmd == "hash-password":       # DB가 필요 없는 명령
        pw = getpass.getpass("배포 서버에서 쓸 관리자 비밀번호(8자 이상): ")
        if len(pw) < 8 or pw != getpass.getpass("한 번 더: "):
            sys.exit("8자 이상이어야 하고, 두 번 입력한 값이 같아야 합니다.")
        print("\nADMIN_PASSWORD_HASH 에 아래 한 줄을 그대로 넣으세요 (비밀번호 원문은 들어 있지 않음):\n")
        print(password_hash_string(pw))
        return
    if a.cmd == "make-env":
        return make_env()
    db.init()
    if a.cmd == "backup":
        print("백업:", db.backup())
    elif a.cmd == "stats":
        with db.tx() as c:
            for row in c.execute("SELECT status, source, COUNT(*) FROM places GROUP BY status, source ORDER BY status, source"):
                print(f"{row[0]:8} {row[1]:10} {row[2]}")
            print("회원", c.execute("SELECT COUNT(*) FROM users").fetchone()[0], "명 / 처리 안 된 신고",
                  c.execute("SELECT COUNT(*) FROM issues WHERE status = 'open'").fetchone()[0], "건")
    elif a.cmd == "export":
        if os.path.exists(a.path):
            sys.exit(f"{a.path} 이(가) 이미 있습니다. 다른 이름을 쓰세요.")
        with db.tx() as c, open(a.path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["이름", "위도", "경도", "주소", "확인일", "출처", "구분", "지붕", "재떨이", "형태"])
            n = 0
            for r in c.execute("SELECT * FROM places WHERE status = 'active' ORDER BY id"):
                w.writerow([r["name"], r["lat"], r["lon"], r["address"], r["verified_at"], r["note"], r["source"],
                            r["roof"], r["ashtray"], r["setting"]])
                n += 1
        print(f"{n}곳을 {a.path} 로 내보냈습니다.")
    elif a.cmd == "import":
        with db.tx() as c:
            n = db.import_csv(c, a.path, a.source, actor="manage")
            db.audit(c, "manage", "import", a.path, f"{n}곳 ({a.source})")
        print(f"{n}곳을 가져왔습니다. (구분: {a.source})")
    elif a.cmd == "passwd":
        pw = getpass.getpass("새 비밀번호: ")
        if pw != getpass.getpass("한 번 더: "):
            sys.exit("두 비밀번호가 다릅니다.")
        set_local_account(a.login_id, pw)
        print(f"'{a.login_id}' 계정의 비밀번호를 저장했습니다.")


def make_env():
    """config.json, data/secret.key, data/local_accounts.json 의 값을 .env 한 파일로 모은다. 값은 화면에 출력하지 않는다."""
    path = envfile.ENV_PATH
    if os.path.exists(path):
        sys.exit(f"{path} 이(가) 이미 있습니다. 덮어쓰지 않으니, 지우거나 이름을 바꾼 뒤 다시 실행하세요.")
    cfg = load_json(CONFIG_PATH, {})
    vals = {}
    try:
        vals["SESSION_SECRET"] = open(SECRET_PATH).read().strip()
    except FileNotFoundError:
        import secrets
        vals["SESSION_SECRET"] = secrets.token_hex(32)
    accounts = load_json(LOCAL_PATH, {})
    if accounts:
        lid, acc = next(iter(accounts.items()))       # 첫 번째 관리자 계정 (해시 그대로 옮김, 원문 없음)
        vals["ADMIN_LOGIN_ID"] = lid
        vals["ADMIN_PASSWORD_HASH"] = f"pbkdf2${acc['salt']}${acc['hash']}"
    vals["ADMIN_EMAILS"] = ",".join(cfg.get("admins", []))
    for p in ("google", "naver", "kakao"):
        c = cfg.get(p, {})
        vals[f"{p.upper()}_CLIENT_ID"] = c.get("client_id", "")
        vals[f"{p.upper()}_CLIENT_SECRET"] = c.get("client_secret", "")
    vals["NOTIFY_WEBHOOK_URL"] = cfg.get("notify", {}).get("webhook_url", "")
    if cfg.get("tiles", {}).get("url", "").startswith("https://tile.openstreetmap.org") is False:
        vals["TILES_URL"] = cfg.get("tiles", {}).get("url", "")
        vals["TILES_ATTRIBUTION"] = cfg.get("tiles", {}).get("attribution", "")
    lines = ["# 천안 흡연장 찾기 설정 — python3 manage.py make-env 로 만듦. GitHub 에 올리지 마세요.", ""]
    lines += [f'{k}="{v}"' if any(ch in v for ch in " #'\"") else f"{k}={v}" for k, v in vals.items()]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"{path} 을(를) 만들었습니다 (본인만 읽기 가능). 들어간 항목:")
    for k, v in vals.items():
        print(f"  {k:22} {'설정됨' if v else '(비어 있음)'}")
    if len(accounts) > 1:
        print(f"  ※ 관리자 계정이 {len(accounts)}개라 첫 번째만 옮겼습니다.")
    print("Render 에서는 Environment → 'Add from .env' 에 이 파일 내용을 붙여넣으세요.")


if __name__ == "__main__":
    main()
