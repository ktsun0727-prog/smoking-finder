# 코드 둘러보기 (발표용)

5분 안에 "이 프로젝트가 어떻게 돌아가는지" 설명할 때 보는 순서입니다. 줄 번호는 2026-11-26 기준입니다.

## 전체 구조

```
 휴대폰 · PC 브라우저 / 안드로이드 앱(WebView)
        │  ① GET /api/places  (흡연장 목록 전체 — 내 위치는 보내지 않음)
        │  ② 거리 계산 · 정렬은 기기 안에서
        ▼
 server.py  (Python 표준 라이브러리 웹 서버, API 약 26개)
   ├─ auth.py       로그인: 구글 · 네이버 OAuth, 관리자 아이디/비밀번호, 서명 쿠키
   ├─ db.py         SQLite (places · users · confirmations · issues · photos · feedback · audit)
   └─ cloudsync.py  무료 서버가 초기화돼도 데이터가 남도록 GitHub 비공개 저장소에 자동 보관
```

## 1. 위치는 서버로 보내지 않는다 — 개인정보를 설계로 지킨다

- 화면은 흡연장 목록을 통째로 받고([index.html:1023](../static/index.html#L1023)), 거리는 기기 안에서 계산합니다.
- [index.html:510 `haversine()`](../static/index.html#L510): 지구 위 두 점 사이 거리(하버사인 공식)
- [index.html:515 `compute()`](../static/index.html#L515): 반경 필터 → 가까운 순 정렬 → 상위 10곳
- 그래서 서버에는 "누가 어디 있었는지"가 **아예 남지 않습니다.** 서버 쪽에도 같은 공식이 있습니다([find_smoking.py:25](../find_smoking.py#L25)). 제보할 때 근처 중복을 찾는 데만 씁니다.

## 2. 제보 → 3명 확인 → 자동 공개 — 가짜 제보 걸러내기

- [server.py:532 `add_place()`](../server.py#L532): 제보 접수. 이미 '확인 중'인 곳 근처면 새로 만들지 않고 **확인으로 합칩니다**.
- [server.py:570 `_confirm()`](../server.py#L570): 같은 사람은 한 번만 확인할 수 있고, `rules.required`(기본 3)명이 모이면 `status = 'active'`로 공개합니다.
- 공개·숨김은 모두 `audit` 표에 기록됩니다(누가 언제 무엇을 했는지).
- 확인 인원은 `CONFIRM_REQUIRED` 환경변수로 바꿀 수 있습니다.

## 3. 보안 — 작은 서비스라도 기본은 지킨다

| 무엇을 | 어디서 |
|---|---|
| 다른 사이트에서 온 쓰기 요청 거부 (CSRF) | [server.py:446](../server.py#L446) `Origin` 검사 |
| 요청 폭주 막기 (IP당 분당 60회, 제보 하루 10건) | [server.py:58 `RateLimiter`](../server.py#L58) |
| 로그인 쿠키 위조 방지 (HMAC 서명) | [auth.py:141 `_sign()`](../auth.py#L141), [auth.py:147 `make_cookie()`](../auth.py#L147) |
| 관리자 비밀번호는 원문 없이 해시만 (PBKDF2) | [auth.py:55](../auth.py#L55) |
| 사진의 촬영 위치(EXIF) 지우기 | [server.py:107 `strip_jpeg_metadata()`](../server.py#L107) |
| 앱 로그인: 1회용 토큰(2분) | [auth.py:209 `issue_app_token()`](../auth.py#L209) |

## 4. 운영비 0원으로 버티기

- **Render 무료 서버**: 재배포하면 저장된 파일이 지워집니다.
- [cloudsync.py](../cloudsync.py): 데이터가 바뀌면 표시만 해 두고 2분마다 `app.db`를 비공개 GitHub 저장소에 올립니다. 서버가 꺼질 때(SIGTERM) 한 번 더 올리고, 다시 켜지면 내려받아 복원합니다([cloudsync.py:102 `restore()`](../cloudsync.py#L102)).
- 지도는 OpenStreetMap, 길찾기는 카카오맵·네이버지도·구글 지도의 **무료 링크**만 씁니다(API 키 없음).

## 5. 테스트

```bash
python3 -m unittest discover -s tests -v
```

- [tests/test_api.py](../tests/test_api.py): 임시 폴더에 서버를 띄워 실제 HTTP로 시험합니다.
  - 로그인 필요 여부, 다른 사이트 요청 거부
  - 3명 확인 공개, 근처 제보 합치기
  - 신고 3번이면 자동 숨김, 관리자 권한
  - 회원 탈퇴, 사진, 앱 로그인 토큰, 한글 이름 로그인
- [tests/test_cloudsync.py](../tests/test_cloudsync.py): 보관 기능이 꺼져 있을 때 아무것도 하지 않는지 확인합니다.

## 6. 데이터는 어떻게 모았나

- 천안시·공공데이터포털에 흡연구역 목록이 없어서 **직접 모았습니다**.
  - 현장 확인
  - 거리뷰로 좌표 계산 (오차 약 ±30m)
  - OpenStreetMap 태그
  - 뉴스 기사
- 출처가 약한 곳은 **'미확인 후보'** 로 따로 표시해서([seed/places.csv](../seed/places.csv) `구분=candidate`) 확인된 곳과 섞이지 않게 했습니다.
