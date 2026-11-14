#!/bin/bash
# 서버 밖으로 백업 복사. 디스크가 고장 나도 데이터를 살릴 수 있게 한다.
#   BACKUP_DEST=/mnt/usb/smoking-backup        ./deploy/backup_offsite.sh   # 다른 디스크
#   BACKUP_DEST=me@다른서버:/home/me/backup    ./deploy/backup_offsite.sh   # 다른 서버 (ssh 키 필요)
# 매일 자동 실행은 deploy/crontab.example 참고.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${BACKUP_DEST:?BACKUP_DEST 를 정해 주세요 (예: /mnt/usb/smoking-backup)}"
DATA="${SMOKING_DATA_DIR:-data}"
latest=$(python3 manage.py backup | sed -n 's/^백업: //p')
echo "새 백업: $latest"
if [[ "$BACKUP_DEST" == *:* ]]; then
  rsync -a "$latest" "$DATA/local_accounts.json" "$DATA/secret.key" "$BACKUP_DEST/"
  [ -d "$DATA/photos" ] && rsync -a "$DATA/photos" "$BACKUP_DEST/"
else
  mkdir -p "$BACKUP_DEST"
  cp -p "$latest" "$DATA/local_accounts.json" "$DATA/secret.key" "$BACKUP_DEST/"
  [ -d "$DATA/photos" ] && cp -rp "$DATA/photos" "$BACKUP_DEST/"
fi
echo "복사 완료 → $BACKUP_DEST"
