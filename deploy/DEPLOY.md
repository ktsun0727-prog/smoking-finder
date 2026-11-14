# 인터넷 공개 가이드

순서대로 따라 하면 됩니다. `smoke.example.com`은 실제 도메인으로 바꾸세요. ★ 표시는 **직접 계정·결제가 필요한 단계**입니다.

## 0. 준비물

| 준비물 | 비고 |
|---|---|
| ★ 리눅스 서버 (Ubuntu) | 클라우드 VM. 이 프로그램은 매우 가벼워서 가장 작은 사양으로 충분 |
| ★ 도메인 | 예: `smoke.example.com` |
| 서버의 포트 80, 443 개방 | 클라우드 방화벽(보안 그룹) + 서버 방화벽 |

## 1. 도메인 연결 ★
도메인 관리 화면에서 **A 레코드**를 서버의 공인 IP로 지정합니다. 반영까지 몇 분~몇 시간 걸립니다.
```bash
getent hosts smoke.example.com     # 서버 IP가 나오면 연결된 것
```

## 2. 프로그램 올리기
```bash
# 서버에서
cd ~ && git clone <저장소 주소> smoking_finder      # 또는 scp/rsync로 폴더 복사
cd smoking_finder
python3 -m unittest tests.test_api                    # 테스트 통과 확인
```
기존 데이터를 옮길 때는 `data/` 폴더 전체(특히 `app.db`, `secret.key`, `local_accounts.json`, `photos/`)를 함께 복사합니다.

## 3. 설정 파일 (`config.json`)
```bash
cp config.example.json config.json && nano config.json
```
- **소셜 로그인 키 ★**: 각 개발자 사이트에서 앱을 만들고 콜백 주소를 등록합니다.
  - 구글: `https://smoke.example.com/auth/google/callback`
  - 네이버: `https://smoke.example.com/auth/naver/callback` (서비스 URL: `https://smoke.example.com`)
- **admins**: 관리자 이메일 또는 `google:회원번호`
- **tiles ★**: 지도 그림 제공 업체 주소 (OpenStreetMap 기본 서버는 대규모 서비스에 쓸 수 없음)
- **notify.webhook_url**: 디스코드/슬랙 채널의 웹훅 주소 (새 제보·신고·사진·문의·서버 오류 알림)

## 4. 개발자 비밀번호 새로 정하기
```bash
python3 manage.py passwd 아이디
```

## 5. 자동 실행 (systemd)
```bash
sudo cp deploy/smoking-finder.service /etc/systemd/system/
sudo nano /etc/systemd/system/smoking-finder.service    # 도메인, User, 경로 확인
sudo systemctl daemon-reload
sudo systemctl enable --now smoking-finder
systemctl status smoking-finder
```

## 6. HTTPS (Caddy)
```bash
# Caddy 설치: https://caddyserver.com/docs/install#debian-ubuntu-raspbian
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile                           # 도메인 바꾸기
sudo mkdir -p /var/log/caddy && sudo systemctl reload caddy
```
브라우저에서 `https://smoke.example.com` 이 열리면 성공입니다. 인증서는 Caddy가 자동으로 받고 갱신합니다.

## 7. 백업과 감시
```bash
crontab -e      # deploy/crontab.example 내용을 붙여 넣고 경로·도메인·백업 위치 수정
```
- 매일: `deploy/backup_offsite.sh` 가 백업을 서버 밖(다른 디스크·다른 서버)으로 복사
- 5분마다: `monitor.py` 가 서버를 확인하고, 멈추면 웹훅으로 알림

## 8. 공개 전 마지막 확인

- [ ] `static/privacy.html`, `static/terms.html` 의 노란 칸(운영자, 연락처, 호스팅 업체, 시행일) 채우기 ★ 검토
- [ ] 위치기반서비스 신고 대상인지 확인 ★ (이용자 위치는 서버로 보내지 않게 만들어져 있음)
- [ ] 휴대폰에서 `https://도메인` 접속 → 내 위치 찾기, 로그인, 제보, 사진 올리기 해 보기
- [ ] `--dev-login` 옵션 없이 실행 중인지 확인
- [ ] 안드로이드 앱을 실제 도메인으로 다시 빌드 (`android/README.md`)

## 문제가 생기면
```bash
journalctl -u smoking-finder -n 100      # 흡연장 서버 기록
journalctl -u caddy -n 100               # HTTPS/프록시 기록
curl -s https://smoke.example.com/healthz
```
