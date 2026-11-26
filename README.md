# 천안 흡연장 찾기

> **피울 곳은 줄었는데, 찾을 방법은 없었다.** 천안에는 공개된 흡연구역 목록이 없고, 2026년부터 정류장·광장까지 금연구역이 됐습니다.
> 이 서비스는 내 위치에서 가까운 흡연구역을 거리순으로 보여 주고, 길찾기·로드뷰로 바로 이어 줍니다.

- **배포 주소**: https://smoking-finder.onrender.com (무료 서버라 첫 접속에 최대 50초)
- **만든 사람**: 김동환 · 단국대학교 · 1팀 (2026 개인과제)
- **특징**: Python 표준 라이브러리만 사용 → `pip install` 없이 `python3` 하나로 실행 · 운영비 0원

## ▶ 발표용: 명령 하나로 시연하기

```bash
cd smoking_finder
python3 demo.py          # → http://localhost:9090 (브라우저 자동 열림)
```

- 실제 데이터와 키는 건드리지 않고, **임시 폴더에 깨끗한 데이터베이스**를 만들어 켭니다. 끄면(`Ctrl + C`) 사라집니다.
- 로그인 키 없이 **개발용 로그인**으로 일반 사용자·관리자를 바꿔 가며 보여 줄 수 있습니다 (주소는 실행 화면에 나옴).
- **"3명 확인" 흐름을 바로 보여 주기 위해** 2명이 이미 확인한 제보 하나를 넣어 둡니다 → 발표자가 세 번째로 '확인'을 누르면 그 자리에서 공개됩니다.

| 순서 | 보여 줄 것 | 핵심 코드 |
|---|---|---|
| 1 | 노란 표지판 첫 화면 → '내 위치로 찾기' | `static/index.html` 위치 요청 |
| 2 | 가까운 순 목록 · 거리 · 도보 시간 → 길찾기/로드뷰 | `static/index.html` `haversine()` · `compute()` (**위치는 기기 안에서만 계산**) |
| 3 | 일반 사용자 로그인 → 시연 제보 '확인' → 3/3 공개 | `server.py` `_confirm()` |
| 4 | 관리자 로그인 → 관리자 화면 | `server.py` `/api/admin/*` |
| 5 | 자동 테스트 21개 통과 | `python3 -m unittest discover -s tests` |

코드를 어디부터 설명하면 좋은지는 **[docs/CODE_TOUR.md](docs/CODE_TOUR.md)** 에 정리했습니다.

## 숫자로 보기

| 항목 | 값 |
|---|---|
| 등록된 흡연구역 | 35곳 (확인 22 · 미확인 후보 13) — `seed/places.csv` |
| 자동 테스트 | 21개 (로그인 · 제보 · 3명 확인 · 권한 · 앱 로그인 · 데이터 보관) |
| 코드 | 약 3,500줄 (서버 Python · 화면 HTML/JS · 안드로이드 Java) |
| 외부 패키지 | 0개 (서버) |
| 운영비 | 0원 (Render 무료 · OpenStreetMap · GitHub 비공개 저장소 백업) |

## 실행

```bash
cd ~/smoking_finder
python3 server.py                 # http://localhost:9090
python3 server.py --dev-login     # 소셜 로그인 키 없이 테스트용 로그인 켜기 (이 PC에서만 동작)
```

끄기: 실행한 터미널에서 `Ctrl + C`

| 옵션 | 뜻 |
|---|---|
| `--port 8080` | 다른 포트로 실행 |
| `--required 3` | 제보가 자동 공개되는 데 필요한 확인 인원 |
| `--gone 3` | "없어졌어요" 신고가 몇 명 모이면 자동으로 숨길지 |
| `--public-url https://도메인` | 인터넷 공개 주소 (로그인 콜백, HTTPS 전용 쿠키에 사용) |
| `--behind-proxy` | nginx/Caddy 뒤에서 실행할 때 (실제 접속 IP를 읽음) |

## 설정 파일 (.env)

`python3 manage.py make-env` 로 지금 설정을 `.env` 하나로 모을 수 있습니다. 항목은 `.env.example` 참고, `.env` 는 GitHub 에 올라가지 않습니다. 배포는 `deploy/RENDER.md`.

## 관리 명령

```bash
python3 manage.py stats                          # 현황
python3 manage.py backup                         # 즉시 백업 (서버도 하루 1번 자동 백업, 최근 14개 보관)
python3 manage.py export 내보내기.csv             # 공개 중인 흡연장을 CSV로
python3 manage.py import 파일.csv --source admin  # CSV 가져오기 (admin / user / candidate / synthetic)
python3 manage.py passwd 아이디                  # 개발자(관리자) 비밀번호 바꾸기
python3 -m unittest discover -s tests -v         # 자동 테스트 21개 (임시 폴더에서 실행, 실제 데이터는 안 건드림)
```

## 기능

- **찾기**: 내 위치(GPS)·지도 클릭·좌표 입력 → 가까운 10곳, 반경 300m/500m/1km/전체, 이름 검색, 즐겨찾기
- **길찾기·거리뷰**: 카카오맵·네이버지도 앱·구글 지도 / 카카오 로드뷰·구글 스트리트뷰 (모두 무료 링크)
- **제보**: 로그인 → 지도에서 위치 지정 → "확인 중" → 다른 이용자 확인 3명 또는 관리자 승인 시 공개
- **정보 관리**: "👍 아직 있어요"(확인 날짜 갱신), "정보가 달라요"(없어짐·위치·정보 신고), 180일 넘은 정보 표시
- **관리자 화면**: 승인 대기 / 신고 처리 / 숨김·복구·삭제 / 회원 이용 제한 / 작업 기록
- **회원**: 구글·네이버 로그인(키 필요), 개발자 로그인(아이디·비밀번호), 회원 탈퇴
- **사진**: 흡연장 사진 올리기 (촬영 위치 정보 자동 삭제, 관리자 검토 후 공개)
- **문의하기**: 로그인 없이 운영자에게 의견 보내기
- **영어 화면**: 기기 언어가 한국어가 아니면 영어로, 아래쪽 버튼으로 전환 (관리자 화면은 한국어)
- **관리자 알림**: 처리할 일 개수 표시 + (선택) 디스코드/슬랙 웹훅 알림
- **서버 감시**: `/healthz`, `monitor.py` (멈추면 알림), 처리 중 오류 시 알림
- **앱**: 안드로이드 앱(`android/`, APK 빌드 가능) + "홈 화면에 추가"(PWA)

## 개인정보 처리 방식 (코드로 지키는 것)

- 이용자의 **현재 위치는 서버로 보내지 않습니다.** 화면이 흡연장 목록(`/api/places`)을 받아 기기 안에서 거리를 계산합니다.
- 서버 로그에는 주소의 `?` 뒤를 지우고 남기며, 접속 IP도 적지 않습니다.
- 요청 제한용 IP 기록은 메모리에만 있고 하루가 지나면 지웁니다.
- 공개 목록에는 누가 올렸는지가 나가지 않습니다.
- 회원 탈퇴 시 회원 정보·즐겨찾기는 삭제하고, 제보·확인 기록은 작성자를 알 수 없게 바꿉니다.
- 개발자 비밀번호는 해시(PBKDF2)만 저장합니다.

## 파일 구조

```
server.py          웹 서버와 API
db.py              SQLite 데이터베이스 (data/app.db), 예전 CSV 자동 이전, 백업
auth.py            로그인(소셜·개발자), 회원, 쿠키
cloudsync.py       무료 서버용 데이터 보관 (비공개 GitHub 저장소에 자동 저장·복원)
demo.py            발표용 데모 서버 (임시 데이터 + 시연용 제보)
find_smoking.py    거리 계산, CSV 읽기, 터미널 검색 도구
manage.py          관리 명령
monitor.py         서버 감시 (멈추면 웹훅 알림)
deploy/            공개용 설정: systemd 서비스, Caddy(HTTPS), 외부 백업, cron, DEPLOY.md
android/           안드로이드 앱 프로젝트 (android/README.md)
static/            화면(index.html), 약관·개인정보처리방침, 앱 설치 파일(manifest, sw.js, icon.svg)
tests/            자동 테스트 (test_api.py, test_cloudsync.py)
seed/places.csv    처음 켤 때 넣는 흡연장 목록 (개인정보 없음)
docs/CODE_TOUR.md  발표용 코드 설명
data/              실제 데이터 (공유 금지: app.db, secret.key, local_accounts.json, backups/)
config.json        로그인 키·관리자·지도 설정 (config.example.json 복사해서 만듦, 공유 금지)
```

## API

모든 응답은 JSON. 쓰기 요청(POST)은 로그인 쿠키가 필요하고, 다른 사이트에서 온 요청은 거부합니다.

| 요청 | 설명 |
|---|---|
| `GET /api/places` | 공개 흡연장 전체 (로그인 시 확인 중인 제보·즐겨찾기 포함) |
| `GET /api/status`, `/api/config`, `/api/me` | 현황 / 지도·규칙 설정 / 내 정보 |
| `POST /api/places` | 제보 `{name, lat, lon, memo, roof, ashtray, setting}` |
| `POST /api/places/{id}/confirm` | 확인 중인 제보에 "나도 확인" |
| `POST /api/places/{id}/issue` | `{kind: still_here | gone | moved | info, memo}` |
| `POST /api/places/{id}/favorite` | 즐겨찾기 켜기/끄기 |
| `POST /api/places/{id}/photo` | 사진 올리기 `{image: "data:image/jpeg;base64,..."}` |
| `POST /api/feedback` | 문의 `{category, message, contact}` (로그인 선택) |
| `GET /photos/{파일}` | 공개된 사진 |
| `GET /healthz` | 서버 상태 점검 |
| `POST /api/me/delete` | 회원 탈퇴 |
| `GET /api/admin/queue` | 관리자: 대기·신고·사진·문의·숨김·회원·기록 |
| `GET /api/admin/summary` | 관리자: 처리할 일 개수 |
| `POST /api/admin/photos/{id}` | 관리자: `{action: approve | delete}` |
| `POST /api/admin/feedback/{id}/done` | 관리자: 문의 처리 완료 |
| `POST /api/admin/places/{id}` | 관리자: `{action: approve | reject | hide | restore | delete | edit, ...}` |
| `POST /api/admin/issues/{id}/resolve` | 관리자: `{hide: true/false}` |
| `POST /api/admin/users/{id}/ban` | 관리자: `{banned: true/false}` |

제한: 제보 하루 10건, 확인·신고 시간당 30건, IP당 쓰기 요청 분당 60건, 개발자 로그인 10분에 5번 실패 시 잠금.

## 인터넷에 공개하기

단계별 안내는 **[deploy/DEPLOY.md](deploy/DEPLOY.md)** 에 있습니다. 필요한 설정 파일(systemd, Caddy, 백업, cron)도 `deploy/` 에 준비돼 있습니다.
