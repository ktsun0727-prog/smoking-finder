"""프로젝트 폴더의 .env 파일을 읽어 환경변수로 넣는다. (표준 라이브러리만 사용)

- 형식: 한 줄에 KEY=VALUE, # 으로 시작하면 주석, 값의 앞뒤 따옴표는 지운다. 'export KEY=VALUE' 도 된다.
- 이미 있는 환경변수는 덮어쓰지 않는다 → Render 대시보드에 넣은 값이 .env 보다 우선.
- .env 는 .gitignore 에 들어 있어 GitHub 에 올라가지 않는다. 항목 목록은 .env.example 참고.
- 이 모듈은 import 되는 순간 실행된다. db·auth 보다 먼저 import 해야 SMOKING_DATA_DIR 등이 적용된다.
"""
import os

ENV_PATH = os.environ.get("SMOKING_ENV_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def parse(text):
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        elif val.startswith("#"):               # 값 없이 주석만 있는 줄 (KEY=   # 설명)
            val = ""
        elif " #" in val:                       # 따옴표 없는 값 뒤의 주석
            val = val.split(" #", 1)[0].rstrip()
        if key.replace("_", "").isalnum():
            out[key] = val
    return out


def load(path=ENV_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            values = parse(f.read())
    except FileNotFoundError:
        return 0
    n = 0
    for k, v in values.items():
        if k not in os.environ and v != "":
            os.environ[k] = v
            n += 1
    return n


LOADED = load()
