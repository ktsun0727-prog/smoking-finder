"""데이터베이스(SQLite). Python 표준 라이브러리에 포함돼 있어 따로 설치할 것이 없다.

처음 실행할 때 예전 CSV/JSON 파일(smoking_areas.csv, user_reports.csv, candidates.csv,
pending.json, users.json)을 읽어 data/app.db 로 옮기고, 원본은 data/legacy_files/ 로 옮겨 보관한다.
"""
import contextlib
import glob
import json
import os
import shutil
import sqlite3
import threading
import time

import cloudsync
from find_smoking import ADDR_KEYS, LAT_KEYS, LON_KEYS, NAME_KEYS, pick, read_rows

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("SMOKING_DATA_DIR") or os.path.join(BASE, "data")
DB_PATH = os.path.join(DATA, "app.db")
BACKUP_DIR = os.path.join(DATA, "backups")
PHOTO_DIR = os.path.join(DATA, "photos")
SEED = os.path.join(BASE, "seed", "places.csv")   # GitHub 에 함께 올리는 초기 흡연장 목록 (개인정보 없음)
KEEP_BACKUPS = 14

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, provider TEXT, name TEXT, email TEXT DEFAULT '',
    role TEXT DEFAULT 'user', banned INTEGER DEFAULT 0, created_at TEXT, last_login TEXT);
CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
    address TEXT DEFAULT '', note TEXT DEFAULT '',
    source TEXT NOT NULL,            -- admin(관리자 등록) / user(이용자 제보) / candidate(미확인 후보) / synthetic(가상)
    status TEXT NOT NULL,            -- active(공개) / pending(확인 중) / hidden(숨김) / rejected(거절)
    roof TEXT DEFAULT '', ashtray TEXT DEFAULT '', setting TEXT DEFAULT '',   -- yes/no/'' , indoor/outdoor/''
    created_by TEXT, created_at TEXT, verified_at TEXT, approved_how TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS confirmations (
    place_id INTEGER REFERENCES places(id) ON DELETE CASCADE, user_id TEXT, created_at TEXT,
    PRIMARY KEY (place_id, user_id));
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id INTEGER REFERENCES places(id) ON DELETE CASCADE, user_id TEXT,
    kind TEXT,                       -- gone(없어짐) / moved(위치 다름) / info(정보 틀림) / still_here(아직 있음)
    memo TEXT DEFAULT '', status TEXT DEFAULT 'open', created_at TEXT, resolved_by TEXT, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS favorites (
    user_id TEXT, place_id INTEGER REFERENCES places(id) ON DELETE CASCADE, PRIMARY KEY (user_id, place_id));
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id INTEGER REFERENCES places(id) ON DELETE CASCADE, user_id TEXT,
    file TEXT NOT NULL,              -- data/photos/ 안의 파일 이름
    status TEXT DEFAULT 'pending',   -- pending(검토 중) / approved(공개)
    created_at TEXT);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT,
    category TEXT, message TEXT, contact TEXT DEFAULT '',
    status TEXT DEFAULT 'open', created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_places_status ON places(status);
CREATE INDEX IF NOT EXISTS idx_photos_place ON photos(place_id, status);
CREATE INDEX IF NOT EXISTS idx_issues_place ON issues(place_id, status);
"""


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def today():
    return time.strftime("%Y-%m-%d")


@contextlib.contextmanager
def tx():
    """연결을 열고, 끝나면 저장(commit)하고 닫는다. 오류가 나면 되돌린다."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        changed = conn.total_changes
        conn.commit()
        if changed:
            cloudsync.mark_dirty()     # GitHub 보관함에 올릴 게 생겼다고 표시
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def audit(conn, actor, action, target, detail=""):
    conn.execute("INSERT INTO audit(at, actor, action, target, detail) VALUES (?,?,?,?,?)",
                 (now(), actor, action, str(target), detail))


def init():
    os.makedirs(DATA, exist_ok=True)
    cloudsync.restore(DB_PATH, PHOTO_DIR)   # 무료 서버가 초기화됐으면 GitHub 보관함에서 되살린다
    first = not os.path.exists(DB_PATH)
    with tx() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(SCHEMA)
        if first:
            moved = migrate_legacy(c)
            if moved:
                print(f"[DB] 예전 파일 {len(moved)}개를 app.db 로 옮겼습니다: {', '.join(moved)}")
            elif os.path.exists(SEED):
                n = import_seed(c, SEED)
                print(f"[DB] 새 데이터베이스에 초기 흡연장 {n}곳을 넣었습니다 ({os.path.basename(SEED)})")
    if first:
        legacy = os.path.join(DATA, "legacy_files")
        for name in ("smoking_areas.csv", "user_reports.csv", "candidates.csv", "pending.json", "users.json"):
            p = os.path.join(DATA, name)
            if os.path.exists(p):
                os.makedirs(legacy, exist_ok=True)
                shutil.move(p, os.path.join(legacy, name))


def import_csv(c, path, source, status="active", actor="import"):
    rows, fields, _ = read_rows(path)
    lat_c, lon_c = pick(fields, LAT_KEYS), pick(fields, LON_KEYS)
    name_c, addr_c = pick(fields, NAME_KEYS), pick(fields, ADDR_KEYS)
    note_c, date_c = pick(fields, ["출처", "메모"]), pick(fields, ["확인일"])
    n = 0
    for r in rows:
        try:
            lat, lon = float(r[lat_c]), float(r[lon_c])
        except (TypeError, ValueError, KeyError):
            continue
        c.execute("""INSERT INTO places(name, lat, lon, address, note, source, status, created_by, created_at, verified_at, approved_how)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  ((r.get(name_c) or "이름 없음").strip(), lat, lon, (r.get(addr_c) or "") if addr_c else "",
                   (r.get(note_c) or "") if note_c else "", source, status, actor, now(),
                   (r.get(date_c) or "") if date_c else "", "가져오기"))
        n += 1
    return n


def import_seed(c, path):
    """seed/places.csv: manage.py export 형식. '구분' 열(admin/user/candidate)대로 넣는다."""
    rows, fields, _ = read_rows(path)
    n = 0
    for r in rows:
        try:
            lat, lon = float(r["위도"]), float(r["경도"])
        except (KeyError, ValueError):
            continue
        source = (r.get("구분") or "admin").strip()
        if source not in ("admin", "user", "candidate"):
            continue                                   # 가상(synthetic) 데이터는 넣지 않는다
        c.execute("""INSERT INTO places(name, lat, lon, address, note, source, status, roof, ashtray, setting,
                     created_by, created_at, verified_at, approved_how) VALUES (?,?,?,?,?,?, 'active', ?,?,?, 'seed', ?, ?, '초기 데이터')""",
                  (r.get("이름", "이름 없음"), lat, lon, r.get("주소", ""), r.get("출처", ""), source,
                   r.get("지붕", ""), r.get("재떨이", ""), r.get("형태", ""), now(), r.get("확인일", "")))
        n += 1
    return n


def migrate_legacy(c):
    moved = []
    for name, source in (("smoking_areas.csv", "admin"), ("user_reports.csv", "user"), ("candidates.csv", "candidate")):
        p = os.path.join(DATA, name)
        if os.path.exists(p):
            import_csv(c, p, source, actor="legacy")
            moved.append(name)
    p = os.path.join(DATA, "users.json")
    if os.path.exists(p):
        for u in json.load(open(p, encoding="utf-8")).values():
            c.execute("INSERT OR IGNORE INTO users(id, provider, name, email, role, created_at, last_login) VALUES (?,?,?,?,?,?,?)",
                      (u["id"], u.get("provider"), u.get("name"), u.get("email", ""), u.get("role", "user"),
                       u.get("created"), u.get("last_login")))
        moved.append("users.json")
    p = os.path.join(DATA, "pending.json")
    if os.path.exists(p):
        for item in json.load(open(p, encoding="utf-8")):
            if item.get("status") != "pending":   # 승인된 것은 user_reports.csv 에 이미 있다
                continue
            cur = c.execute("""INSERT INTO places(name, lat, lon, note, source, status, created_by, created_at)
                               VALUES (?,?,?,?, 'user', 'pending', ?, ?)""",
                            (item["name"], item["lat"], item["lon"], item.get("memo", ""), item["submitter"], item["created"]))
            for uid in item["confirmers"]:
                c.execute("INSERT OR IGNORE INTO confirmations VALUES (?,?,?)", (cur.lastrowid, uid, item["created"]))
        moved.append("pending.json")
    return moved


def backup():
    """운영 중에도 안전하게 복사하는 SQLite 백업. 최근 14개만 남긴다."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = os.path.join(BACKUP_DIR, time.strftime("app-%Y%m%d-%H%M%S.db"))
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    for old in sorted(glob.glob(os.path.join(BACKUP_DIR, "app-*.db")))[:-KEEP_BACKUPS]:
        os.remove(old)
    return dest


def start_daily_backup():
    def loop():
        while True:
            try:
                print(f"[백업] {backup()}")
            except Exception as e:
                print(f"[백업 실패] {e}")
            time.sleep(24 * 3600)
    threading.Thread(target=loop, daemon=True).start()
