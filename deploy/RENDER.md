# Render 배포 가이드

## 한 번에 관리: .env
모든 설정을 프로젝트 폴더의 `.env` 파일 하나로 관리할 수 있습니다. (`.env` 는 GitHub 에 올라가지 않음)
```bash
python3 manage.py make-env      # 지금 설정(config.json·secret.key·관리자 계정)을 .env 로 모으기
# 또는: cp .env.example .env 후 직접 채우기
```
- 내 PC: 서버가 켜질 때 `.env` 를 자동으로 읽습니다.
- Render: **Environment → Add from .env** 에 `.env` 내용을 통째로 붙여넣으면 아래 환경변수가 한 번에 들어갑니다.
- 우선순위: 실제 환경변수(Render 화면) > `.env` > `config.json`·`data/` 파일

## GitHub에 없는 파일 → Render 환경변수
| 내 PC의 파일 | Render 환경변수 | 넣는 값 |
|---|---|---|
| `data/secret.key` | `SESSION_SECRET` | Render 가 자동 생성 (render.yaml `generateValue`) — 직접 넣을 필요 없음 |
| `data/local_accounts.json` | `ADMIN_LOGIN_ID` | 관리자 로그인 아이디 |
| 〃 | `ADMIN_PASSWORD_HASH` | `python3 manage.py hash-password` 출력값 (`pbkdf2$...`) |
| `config.json` → admins | `ADMIN_EMAILS` | 관리자 이메일, 여러 개면 쉼표로 |
| `config.json` → google | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | 구글 OAuth 키 |
| `config.json` → naver/kakao | `NAVER_CLIENT_ID` … `KAKAO_CLIENT_SECRET` | (선택) |
| `config.json` → notify | `NOTIFY_WEBHOOK_URL` | (선택) 알림 웹훅 |
| `config.json` → tiles | `TILES_URL`, `TILES_ATTRIBUTION` | (선택) 지도 그림 업체 |
| `data/app.db` | — | 환경변수로 못 넣음. 처음 켤 때 `seed/places.csv`(흡연장 21곳)로 자동 채움 |

자동으로 처리되는 것: `PORT`(Render 가 지정) → 0.0.0.0 으로 열기, `RENDER_EXTERNAL_URL` → 공개 주소·구글 콜백·Secure 쿠키, `RENDER` → 프록시 뒤 실행.

## 순서
1. 이 저장소를 GitHub 에 최신으로 올린다.
2. Render → **New → Blueprint** → 저장소 선택 → `render.yaml` 대로 생성. (또는 New → Web Service, Start Command `python3 server.py`)
3. **Environment** 탭에서 `sync: false` 항목들을 입력한다.
4. 구글 클라우드 → 클라이언트 → 승인된 리디렉션 URI 에 `https://<서비스이름>.onrender.com/auth/google/callback` 추가.
5. 배포 후 `https://<서비스이름>.onrender.com/healthz` 가 `{"ok": true ...}` 면 성공.

## 무료 플랜의 한계
- **15분 동안 접속이 없으면 잠들고**, 다음 접속 때 깨어나는 데 30초~1분 걸린다.
- **재배포·재시작하면 `data/` 가 초기화된다.** 제보·회원·사진·즐겨찾기가 사라지고 흡연장은 `seed/places.csv` 로 다시 채워진다.
- 계속 보관하려면 Starter 플랜 + Persistent Disk(render.yaml 주석 참고) 또는 외부 데이터베이스가 필요하다.
