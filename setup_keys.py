"""로그인 키를 config.json 에 안전하게 넣는 도구.

    cd ~/smoking_finder && python3 setup_keys.py

- 키는 터미널에서 직접 입력합니다. 입력하는 글자는 화면에 보이지 않습니다.
- 채팅(Claude)에 키를 붙여넣을 필요가 없습니다.
- config.json 은 본인만 읽을 수 있게(권한 600) 저장됩니다.
- 이미 넣은 값은 Enter만 누르면 그대로 둡니다.
"""
import getpass
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "config.json")
EXAMPLE = os.path.join(BASE, "config.example.json")

PROVIDERS = [
    ("google", "구글", "http://localhost:9090/auth/google/callback"),
    ("naver", "네이버", "http://localhost:9090/auth/naver/callback"),
    ("kakao", "카카오", "http://localhost:9090/auth/kakao/callback"),
]


def ask(label, current, secret):
    shown = "(이미 있음, Enter = 그대로)" if current else "(Enter = 건너뛰기)"
    prompt = f"  {label} {shown}: "
    value = getpass.getpass(prompt) if secret else input(prompt)
    return value.strip() or current


def main():
    if os.path.exists(CONFIG):
        cfg = json.load(open(CONFIG, encoding="utf-8"))
    else:
        cfg = json.load(open(EXAMPLE, encoding="utf-8"))
    print("로그인 키 설정 — 키는 화면에 보이지 않습니다.\n")
    for pid, label, callback in PROVIDERS:
        if input(f"{label} 로그인 키를 넣을까요? [y/N] ").strip().lower() != "y":
            continue
        print(f"  (개발자 사이트에 등록할 콜백 주소: {callback})")
        c = cfg.setdefault(pid, {})
        c["client_id"] = ask("Client ID", c.get("client_id", ""), secret=False)
        c["client_secret"] = ask("Client Secret", c.get("client_secret", ""), secret=True)
        print()

    tmp = CONFIG + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG)
    os.chmod(CONFIG, 0o600)

    print("저장했습니다:", CONFIG)
    for pid, label, _ in PROVIDERS:
        c = cfg.get(pid, {})
        ok = bool(c.get("client_id")) and (bool(c.get("client_secret")) or pid == "kakao")
        print(f"  {label}: {'설정됨' if ok else '없음'}")
    print("\n서버를 다시 켜면 적용됩니다:  python3 server.py")


if __name__ == "__main__":
    main()
