"""소셜 로그인(구글·네이버), 개발자 로그인, 회원, 세션 쿠키.

키는 config.json 에 넣는다 (config.example.json 참고). 키가 없는 로그인 방식은 화면에서 비활성화된다.
세 회사 모두 '로그인' 자체는 무료지만, 각 개발자 사이트에 앱을 등록해 키를 받아야 한다.
Python 표준 라이브러리만 사용한다.

개발자 비밀번호 바꾸기:  python3 auth.py passwd 아이디
"""
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request

import db

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("SMOKING_CONFIG") or os.path.join(BASE, "config.json")
SECRET_PATH = os.path.join(db.DATA, "secret.key")
LOCAL_PATH = os.path.join(db.DATA, "local_accounts.json")   # 아이디·비밀번호 계정 (비밀번호는 해시만 저장)
SESSION_DAYS = 30
PBKDF2_ROUNDS = 200_000
MAX_FAILS, FAIL_WINDOW = 5, 600   # 10분 안에 5번 틀리면 잠시 잠금

PROVIDERS = {
    "google": {"label": "구글", "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
               "token": "https://oauth2.googleapis.com/token",
               "profile": "https://openidconnect.googleapis.com/v1/userinfo", "scope": "openid email profile"},
    "naver": {"label": "네이버", "authorize": "https://nid.naver.com/oauth2.0/authorize",
              "token": "https://nid.naver.com/oauth2.0/token",
              "profile": "https://openapi.naver.com/v1/nid/me", "scope": ""},
}
LABELS = {**{k: v["label"] for k, v in PROVIDERS.items()}, "local": "개발자", "dev": "개발용"}


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS).hex()


def config_from_env(cfg):
    """환경변수가 있으면 config.json 값보다 우선한다 (Render 같은 배포 환경용)."""
    env = os.environ.get
    for p in ("google", "naver"):
        cid, sec = env(f"{p.upper()}_CLIENT_ID"), env(f"{p.upper()}_CLIENT_SECRET")
        if cid or sec:
            c = cfg.setdefault(p, {})
            if cid:
                c["client_id"] = cid.strip()
            if sec:
                c["client_secret"] = sec.strip()
    if env("ADMIN_EMAILS"):
        cfg["admins"] = [a.strip() for a in env("ADMIN_EMAILS").split(",") if a.strip()]
    if env("NOTIFY_WEBHOOK_URL"):
        cfg.setdefault("notify", {})["webhook_url"] = env("NOTIFY_WEBHOOK_URL").strip()
    if env("TILES_URL"):
        cfg.setdefault("tiles", {})["url"] = env("TILES_URL").strip()
        if env("TILES_ATTRIBUTION"):
            cfg["tiles"]["attribution"] = env("TILES_ATTRIBUTION").strip()
    return cfg


def password_hash_string(password):
    """환경변수 ADMIN_PASSWORD_HASH 에 넣을 값: pbkdf2$소금$해시 (비밀번호 원문은 담기지 않음)."""
    salt = secrets.token_bytes(16)
    return f"pbkdf2${salt.hex()}${hash_password(password, salt)}"


def local_accounts():
    """개발자(관리자) 계정: data/local_accounts.json + 환경변수 ADMIN_LOGIN_ID / ADMIN_PASSWORD_HASH."""
    accounts = load_json(LOCAL_PATH, {})
    lid, ph = os.environ.get("ADMIN_LOGIN_ID", "").strip(), os.environ.get("ADMIN_PASSWORD_HASH", "").strip()
    if lid and ph.count("$") == 2 and ph.startswith("pbkdf2$"):
        _, salt, digest = ph.split("$")
        accounts[lid] = {"salt": salt, "hash": digest, "role": "admin", "name": lid}
    return accounts


def set_local_account(login_id, password, role="admin", name=""):
    """아이디·비밀번호 계정을 만들거나 비밀번호를 바꾼다. 비밀번호 원문은 저장하지 않는다."""
    if not login_id or len(password) < 8:
        raise ValueError("아이디를 입력하고, 비밀번호는 8자 이상으로 정하세요.")
    os.makedirs(db.DATA, exist_ok=True)
    accounts = load_json(LOCAL_PATH, {})
    salt = secrets.token_bytes(16)
    accounts[login_id] = {"salt": salt.hex(), "hash": hash_password(password, salt), "role": role,
                          "name": name or login_id, "updated": db.now()}
    save_json(LOCAL_PATH, accounts)
    os.chmod(LOCAL_PATH, 0o600)


class Auth:
    def __init__(self, dev_login=False, secure_cookie=False):
        self.config = config_from_env(load_json(CONFIG_PATH, {}))
        self.dev_login = dev_login
        self.secure_cookie = secure_cookie
        self.states = {}   # OAuth state → (provider, 만료시각)
        self.fails = {}    # 접속 IP → 개발자 로그인 실패 시각들
        os.makedirs(db.DATA, exist_ok=True)
        env_secret = os.environ.get("SESSION_SECRET", "").strip()
        if env_secret:                       # 배포 환경: 재배포해도 로그인이 풀리지 않게 고정 키 사용
            self.secret = env_secret.encode()
            return
        if not os.path.exists(SECRET_PATH):
            with open(SECRET_PATH, "w") as f:
                f.write(secrets.token_hex(32))
            os.chmod(SECRET_PATH, 0o600)
        with open(SECRET_PATH) as f:
            self.secret = f.read().strip().encode()

    # ---------- 설정 ----------
    def enabled(self, p):
        c = self.config.get(p, {})
        return bool(c.get("client_id") and c.get("client_secret"))

    def providers(self):
        return [{"id": p, "label": v["label"], "enabled": self.enabled(p)} for p, v in PROVIDERS.items()]

    def is_admin(self, u):
        admins = set(self.config.get("admins", []))
        return bool(u) and (u["role"] == "admin" or u["id"] in admins or (u["email"] or "") in admins)

    # ---------- 세션 쿠키 (서명된 값: uid.만료.서명) ----------
    def _sign(self, msg):
        return hmac.new(self.secret, msg.encode(), hashlib.sha256).hexdigest()

    def _cookie(self, value, max_age):
        return f"sid={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}" + ("; Secure" if self.secure_cookie else "")

    def make_cookie(self, uid):
        exp = int(time.time()) + SESSION_DAYS * 86400
        val = f"{uid}.{exp}"
        return self._cookie(f"{val}.{self._sign(val)}", SESSION_DAYS * 86400)

    def clear_cookie(self):
        return self._cookie("", 0)

    def user_from_cookie(self, header):
        for part in (header or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k != "sid":
                continue
            try:
                uid, exp, sig = v.rsplit(".", 2)
                if not hmac.compare_digest(sig, self._sign(f"{uid}.{exp}")) or int(exp) < time.time():
                    return None
            except ValueError:
                return None
            with db.tx() as c:
                row = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if not row:
                return None
            u = dict(row)
            u["admin"] = self.is_admin(u)
            return u
        return None

    # ---------- 회원 ----------
    def upsert_user(self, provider, pid, name, email, role=None):
        uid = f"{provider}:{pid}"
        name = (name or "").strip()[:30] or f"{LABELS.get(provider, provider)} 사용자"
        with db.tx() as c:
            c.execute("""INSERT INTO users(id, provider, name, email, created_at, last_login) VALUES (?,?,?,?,?,?)
                         ON CONFLICT(id) DO UPDATE SET name = excluded.name, email = excluded.email, last_login = excluded.last_login""",
                      (uid, provider, name, email or "", db.now(), db.now()))
            if role:
                c.execute("UPDATE users SET role = ? WHERE id = ?", (role, uid))
        return uid

    # ---------- 개발자 로그인 ----------
    def local_login(self, login_id, password, ip):
        now = time.time()
        self.fails = {k: v for k, v in self.fails.items() if v and now - v[-1] < FAIL_WINDOW}   # 지난 기록 정리
        recent = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
        if len(recent) >= MAX_FAILS:
            raise PermissionError("로그인 시도가 너무 많습니다. 10분 뒤에 다시 시도하세요.")
        acc = local_accounts().get(login_id)
        # 계정이 없어도 같은 시간이 걸리게 해서, 아이디 존재 여부가 드러나지 않게 한다
        salt = bytes.fromhex(acc["salt"]) if acc else b"\0" * 16
        ok = hmac.compare_digest(hash_password(password, salt), acc["hash"] if acc else "x" * 64)
        if not (acc and ok):
            self.fails[ip] = recent + [now]
            raise ValueError("아이디 또는 비밀번호가 올바르지 않습니다.")
        self.fails.pop(ip, None)
        return self.upsert_user("local", login_id, acc.get("name") or login_id, "", role=acc.get("role", "admin"))
        

    # ---------- OAuth ----------
    def login_url(self, p, redirect_uri):
        if not self.enabled(p):
            raise ValueError("설정되지 않은 로그인 방식입니다.")
        state = secrets.token_urlsafe(24)
        now = time.time()
        self.states = {k: v for k, v in self.states.items() if v[1] > now}
        self.states[state] = (p, now + 600)
        q = {"response_type": "code", "client_id": self.config[p]["client_id"], "redirect_uri": redirect_uri, "state": state}
        if PROVIDERS[p]["scope"]:
            q["scope"] = PROVIDERS[p]["scope"]
        return PROVIDERS[p]["authorize"] + "?" + urllib.parse.urlencode(q)

    def callback(self, p, code, state, redirect_uri):
        saved = self.states.pop(state or "", None)
        if not saved or saved[0] != p or saved[1] < time.time():
            raise ValueError("로그인 요청이 만료되었거나 올바르지 않습니다. 다시 시도해 주세요.")
        c = self.config[p]
        form = {"grant_type": "authorization_code", "client_id": c["client_id"], "code": code, "redirect_uri": redirect_uri}
        if c.get("client_secret"):
            form["client_secret"] = c["client_secret"]
        if p == "naver":
            form["state"] = state
        token = _post_form(PROVIDERS[p]["token"], form).get("access_token")
        if not token:
            raise ValueError("토큰을 받지 못했습니다.")
        prof = _get_json(PROVIDERS[p]["profile"], token)
        if p == "google":
            pid, name, email = prof.get("sub"), prof.get("name"), prof.get("email")
        else:   # naver
            r = prof.get("response", {})
            pid, name, email = r.get("id"), r.get("nickname") or r.get("name"), r.get("email")
        if not pid:
            raise ValueError("사용자 정보를 받지 못했습니다.")
        return self.upsert_user(p, str(pid), name, email)


def _post_form(url, form):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def _get_json(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


if __name__ == "__main__":
    import getpass
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "passwd":
        pw = getpass.getpass("새 비밀번호: ")
        if pw != getpass.getpass("한 번 더: "):
            sys.exit("두 비밀번호가 다릅니다.")
        set_local_account(sys.argv[2], pw)
        print(f"'{sys.argv[2]}' 계정의 비밀번호를 저장했습니다. (관리자 권한)")
    else:
        print("사용법: python3 auth.py passwd 아이디")
