"""서버 감시: /healthz 를 확인하고, 응답이 없으면 관리자 알림(웹훅)을 보낸다.

    python3 monitor.py https://smoke.example.com          # 한 번 확인 (정상이면 조용히 끝남)

5분마다 자동으로 확인하려면 crontab -e 에 한 줄 추가:
    */5 * * * * cd ~/smoking_finder && python3 monitor.py https://smoke.example.com >> data/monitor.log 2>&1

알림 주소는 config.json 의 notify.webhook_url 을 쓴다. 같은 장애로 알림이 계속 가지 않도록,
처음 멈췄을 때와 다시 살아났을 때만 보낸다.
"""
import json
import os
import sys
import time
import urllib.request

import envfile  # noqa: F401  (.env 의 NOTIFY_WEBHOOK_URL 사용)

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "data", "monitor_state.json")


def notify(text):
    try:
        cfg = json.load(open(os.environ.get("SMOKING_CONFIG") or os.path.join(BASE, "config.json"), encoding="utf-8"))
        url = os.environ.get("NOTIFY_WEBHOOK_URL") or cfg.get("notify", {}).get("webhook_url")
    except (FileNotFoundError, json.JSONDecodeError):
        url = os.environ.get("NOTIFY_WEBHOOK_URL")
    print(time.strftime("%Y-%m-%d %H:%M:%S"), text)
    if url:
        body = json.dumps({"text": text, "content": text}).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}), timeout=10).close()


def main():
    if len(sys.argv) != 2:
        sys.exit("사용법: python3 monitor.py 서버주소")
    base = sys.argv[1].rstrip("/")
    try:
        with urllib.request.urlopen(base + "/healthz", timeout=10) as r:
            ok = r.status == 200 and json.loads(r.read()).get("ok")
    except Exception:
        ok = False
    try:
        was_down = json.load(open(STATE)).get("down", False)
    except (FileNotFoundError, json.JSONDecodeError):
        was_down = False
    if not ok and not was_down:
        notify(f"🔴 흡연장 서버가 응답하지 않습니다: {base}")
    elif ok and was_down:
        notify(f"🟢 흡연장 서버가 다시 정상입니다: {base}")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump({"down": not ok, "checked": time.strftime("%Y-%m-%d %H:%M:%S")}, open(STATE, "w"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
