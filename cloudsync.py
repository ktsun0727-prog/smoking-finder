"""무료 서버(Render)에서도 데이터가 지워지지 않게, 비공개 GitHub 저장소에 데이터베이스와 사진을 보관한다.

환경변수 두 개를 넣으면 켜진다 (없으면 아무것도 하지 않음):
  DATA_REPO        예) ktsun0727-prog/smoking-finder-data   (비공개 저장소)
  DATA_REPO_TOKEN  그 저장소 하나만 Contents 읽기/쓰기 권한이 있는 GitHub 토큰 (fine-grained)

동작
- 서버를 켤 때 로컬에 데이터베이스가 없으면 저장소에서 app.db 와 photos/ 를 내려받는다.
- 데이터가 바뀌면 표시만 해 두고, 2분마다(바뀐 게 있을 때만) app.db 를 올린다.
- 사진은 올라오는 즉시 올리고, 지우면 저장소에서도 지운다.
- Render 가 재배포로 서버를 끌 때(SIGTERM) 마지막으로 한 번 더 올린다.
"""
import base64
import json
import os
import signal
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = os.environ.get("DATA_REPO", "").strip()
TOKEN = os.environ.get("DATA_REPO_TOKEN", "").strip()
BRANCH = os.environ.get("DATA_REPO_BRANCH", "main").strip()
INTERVAL = int(os.environ.get("DATA_SYNC_SECONDS", "120"))
API = "https://api.github.com"

_dirty = threading.Event()
_lock = threading.Lock()          # 올리기는 한 번에 하나씩
_sha = {}                          # 경로 → 저장소에 있는 파일의 sha (덮어쓸 때 필요)
_started = False


def enabled():
    return bool(REPO and TOKEN)


def _req(method, path, body=None, raw=False, timeout=30):
    url = f"{API}/repos/{REPO}/contents/{urllib.parse.quote(path)}"
    if method == "GET":
        url += f"?ref={urllib.parse.quote(BRANCH)}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "smoking-finder-sync",
        **({"Content-Type": "application/json"} if data else {}),
    })
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            out = resp.read()
            return resp.status, (out if raw else (json.loads(out) if out else None))
    except urllib.error.HTTPError as e:
        return e.code, None


def _meta(path):
    """파일의 sha 를 알아 둔다 (없으면 None)."""
    st, j = _req("GET", path)
    if st == 200 and isinstance(j, dict):
        _sha[path] = j.get("sha")
        return j
    _sha.pop(path, None)
    return None


def _put(path, content, message):
    body = {"message": message, "content": base64.b64encode(content).decode(), "branch": BRANCH}
    if path not in _sha:
        _meta(path)
    if _sha.get(path):
        body["sha"] = _sha[path]
    st, j = _req("PUT", path, body, timeout=60)
    if st == 409 or st == 422:          # sha 가 오래됨 → 새로 읽고 한 번 더
        _meta(path)
        if _sha.get(path):
            body["sha"] = _sha[path]
        st, j = _req("PUT", path, body, timeout=60)
    if st in (200, 201) and j:
        _sha[path] = j["content"]["sha"]
        return True
    print(f"[보관] {path} 올리기 실패 (HTTP {st})", file=sys.stderr)
    return False


def _delete(path, message):
    if path not in _sha:
        _meta(path)
    if not _sha.get(path):
        return True
    st, _ = _req("DELETE", path, {"message": message, "sha": _sha[path], "branch": BRANCH})
    _sha.pop(path, None)
    return st in (200, 404)


# ───────── 서버가 켤 때 ─────────
def restore(db_path, photo_dir):
    """로컬에 데이터베이스가 없을 때만 저장소에서 받아 온다. 받아 왔으면 True."""
    if not enabled() or os.path.exists(db_path):
        return False
    st, data = _req("GET", "app.db", raw=True, timeout=60)
    if st != 200 or not data:
        print(f"[보관] 저장소에 app.db 가 아직 없습니다 (HTTP {st}). 새로 시작합니다.")
        return False
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    tmp = db_path + ".download"
    with open(tmp, "wb") as f:
        f.write(data)
    try:                                  # 받은 파일이 정상 SQLite 인지 확인
        c = sqlite3.connect(tmp)
        c.execute("PRAGMA integrity_check").fetchone()
        c.close()
    except sqlite3.DatabaseError as e:
        os.remove(tmp)
        print(f"[보관] 받은 app.db 가 손상됐습니다: {e}", file=sys.stderr)
        return False
    os.replace(tmp, db_path)
    _meta("app.db")
    n = 0
    st, items = _req("GET", "photos")
    if st == 200 and isinstance(items, list):
        os.makedirs(photo_dir, exist_ok=True)
        for it in items:
            name = it.get("name", "")
            if it.get("type") != "file" or "/" in name or name.startswith("."):
                continue
            s2, img = _req("GET", f"photos/{name}", raw=True, timeout=60)
            if s2 == 200 and img:
                with open(os.path.join(photo_dir, name), "wb") as f:
                    f.write(img)
                _sha[f"photos/{name}"] = it.get("sha")
                n += 1
    print(f"[보관] 저장소에서 데이터베이스와 사진 {n}장을 복원했습니다.")
    return True


# ───────── 데이터가 바뀔 때 ─────────
def mark_dirty():
    if enabled():
        _dirty.set()


def push_db(db_path, reason="자동 저장"):
    """SQLite 를 안전하게 복사한 뒤 저장소에 올린다."""
    if not enabled() or not os.path.exists(db_path):
        return False
    with _lock:
        _dirty.clear()
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            src, dst = sqlite3.connect(db_path), sqlite3.connect(tmp)
            with dst:
                src.backup(dst)
            src.close(); dst.close()
            with open(tmp, "rb") as f:
                ok = _put("app.db", f.read(), f"{reason} {time.strftime('%Y-%m-%d %H:%M:%S')}")
        finally:
            os.remove(tmp)
        if not ok:
            _dirty.set()                  # 다음 번에 다시 시도
        return ok


def push_photo(photo_dir, name):
    if enabled():
        threading.Thread(target=lambda: _push_photo(photo_dir, name), daemon=True).start()


def _push_photo(photo_dir, name):
    try:
        with open(os.path.join(photo_dir, name), "rb") as f:
            _put(f"photos/{name}", f.read(), f"사진 추가 {name}")
    except Exception as e:
        print(f"[보관] 사진 올리기 실패: {e}", file=sys.stderr)


def delete_photo(name):
    if enabled():
        threading.Thread(target=lambda: _delete(f"photos/{name}", f"사진 삭제 {name}"), daemon=True).start()


def start(db_path):
    """주기적으로 올리는 스레드와 종료 시 마지막 저장을 켠다."""
    global _started
    if not enabled() or _started:
        return
    _started = True
    if "app.db" not in _sha:
        _meta("app.db")
        if "app.db" not in _sha:          # 저장소가 비어 있으면 첫 데이터베이스를 바로 올린다
            _dirty.set()

    def loop():
        while True:
            time.sleep(INTERVAL)
            if _dirty.is_set():
                try:
                    push_db(db_path)
                except Exception as e:
                    print(f"[보관] 자동 저장 실패: {e}", file=sys.stderr)
    threading.Thread(target=loop, daemon=True).start()

    def on_term(signum, frame):
        if _dirty.is_set():
            print("[보관] 종료 전 마지막 저장")
            try:
                push_db(db_path, "종료 전 저장")
            except Exception as e:
                print(f"[보관] 종료 전 저장 실패: {e}", file=sys.stderr)
        sys.exit(0)
    try:
        signal.signal(signal.SIGTERM, on_term)
    except ValueError:                    # 메인 스레드가 아닐 때(테스트)
        pass
    print(f"[보관] GitHub 저장소 {REPO} 에 {INTERVAL}초마다 자동 저장합니다.")
