# 안드로이드 앱 (흡연장 찾기)

서버의 웹 화면을 띄우는 앱입니다. 웹만으로 어려운 기능은 앱이 맡습니다.

- **GPS**: 앱이 위치를 직접 읽어 화면에 넘깁니다 (위치는 서버로 보내지 않음)
- **사진 올리기**: 갤러리·카메라 앱으로 사진 선택
- **외부 앱 연결**: 카카오맵·네이버지도·구글 지도·거리뷰는 해당 앱이나 브라우저로 열림
- **서버 주소 바꾸기**: 접속이 안 되면 나오는 화면에서 앱 안에서 바로 변경
- **구글·네이버 로그인**: 앱 안에서는 구글·네이버가 로그인을 막아서, 휴대폰 브라우저로 로그인한 뒤 `smokingfinder://login` 으로 앱에 돌아옵니다 (1회용 토큰, 2분 유효)
- 기본 서버: `https://smoking-finder.onrender.com` (HTTPS 전용)
- 외부 라이브러리 없이 안드로이드 기본 기능만 사용

## 빌드 (이 서버에는 도구가 설치돼 있음: `~/android-tools`)

```bash
cd ~/smoking_finder/android
export JAVA_HOME=~/android-tools/jdk ANDROID_HOME=~/android-tools/sdk PATH=~/android-tools/jdk/bin:$PATH

./gradlew assembleDebug                                              # 테스트용
./gradlew assembleRelease -PserverUrl=https://smoke.example.com      # 출시용 (실제 도메인으로)
```
결과물:
- 테스트용: `app/build/outputs/apk/debug/app-debug.apk`
- 출시용: `app/build/outputs/apk/release/app-release.apk` (서명됨)

PC의 Android Studio에서 `android` 폴더를 열어 빌드해도 됩니다.

## 휴대폰에 설치하기

1. APK 파일을 휴대폰으로 옮깁니다 (메신저 나에게 보내기, USB, 클라우드 등).
2. 휴대폰에서 파일을 눌러 설치합니다. "출처를 알 수 없는 앱" 허용을 물으면 허용합니다.
3. 앱을 열면 **서버 주소**로 접속합니다.
   - 기본값은 배포된 `https://smoking-finder.onrender.com` 입니다.
   - 실제 휴대폰에서는 연결 실패 화면의 **"서버 주소 바꾸기"**에서 휴대폰이 접속할 수 있는 주소를 넣습니다.
     예: 공개 서버 `https://smoke.example.com`, 같은 와이파이의 PC `http://192.168.0.10:9090` (PC에서 `python3 server.py --host 0.0.0.0`)

## 출시 서명 키 — 꼭 백업하세요

- `release.jks`(서명 키)와 `keystore.properties`(비밀번호)가 이 폴더에 있습니다. **공유하지 말고, 서버 밖에 안전하게 백업하세요.**
- 이 키를 잃어버리면 플레이스토어에 올린 앱을 **업데이트할 수 없습니다.**
- 두 파일은 `.gitignore`에 들어 있어 저장소에 올라가지 않습니다.

## 플레이스토어 출시 ★

1. Google Play Console 개발자 등록 (1회 25달러)
2. `./gradlew bundleRelease -PserverUrl=https://도메인` → `app/build/outputs/bundle/release/app-release.aab` 업로드
3. 개인정보처리방침 주소(`https://도메인/privacy`), 위치 권한 사용 이유, 앱 설명·스크린샷 입력
4. 출시 전 `app/src/main/res/xml/network_security_config.xml` 의 `cleartextTrafficPermitted` 를 `false` 로 바꿔 HTTPS만 쓰게 합니다.

아이폰 앱은 Mac과 Apple 개발자 계정(연 99달러)이 필요합니다. 그 전까지 아이폰 사용자는 Safari의 "홈 화면에 추가"(PWA)로 설치할 수 있습니다.
