package kr.cheonan.smokingfinder;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.location.Location;
import android.location.LocationManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.CancellationSignal;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.webkit.CookieManager;
import android.webkit.GeolocationPermissions;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.Toast;

import java.util.Locale;

/**
 * 천안 흡연장 찾기 안드로이드 앱.
 * 서버의 웹 화면을 그대로 띄우고, 웹만으로 어려운 일을 앱이 맡는다.
 *  - GPS: 앱이 위치를 읽어 화면에 넘긴다 (http 서버에서도 동작, 위치는 서버로 보내지 않음).
 *  - 사진 선택: 사진 올리기 버튼에서 갤러리/카메라 앱을 연다.
 *  - 외부 링크: 카카오맵·네이버지도·구글 지도·거리뷰는 해당 앱이나 브라우저로 연다.
 *  - 서버 주소: 접속이 안 되면 앱 안에서 바꿀 수 있다.
 *  - 구글·네이버 로그인: 앱 안 화면에서는 막히므로 휴대폰 브라우저로 열고, 끝나면 smokingfinder://login 으로 돌아온다.
 */
public class MainActivity extends Activity {
    private static final int REQ_LOCATION = 1;
    private static final int REQ_FILE = 2;
    private static final String PREFS = "settings";
    private static final String KEY_SERVER = "server_url";

    private WebView web;
    private String serverUrl;
    private volatile String currentUrl = "";
    private ValueCallback<Uri[]> fileCallback;
    private GeolocationPermissions.Callback geoCallback;
    private String geoOrigin;
    private final Handler main = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        serverUrl = trimSlash(prefs.getString(KEY_SERVER, BuildConfig.SERVER_URL));

        web = new WebView(this);
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setGeolocationEnabled(true);
        s.setSupportMultipleWindows(false);        // target=_blank 링크도 이 창에서 처리 → 아래에서 외부로 넘김
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(true);
        s.setUserAgentString(s.getUserAgentString() + " SmokingFinderApp/" + BuildConfig.VERSION_NAME);
        CookieManager.getInstance().setAcceptCookie(true);

        // 화면(JavaScript)에서 window.SmokingApp.requestLocation() 으로 부를 수 있는 통로
        web.addJavascriptInterface(new Bridge(), "SmokingApp");
        web.addJavascriptInterface(new Settings(), "SmokingAppSettings");   // 연결 실패 화면의 '서버 주소 바꾸기' 버튼용
        web.setWebViewClient(new Client());
        web.setWebChromeClient(new Chrome());

        if (handleLoginReturn(getIntent())) return;
        if (savedInstanceState != null) web.restoreState(savedInstanceState);
        else web.loadUrl(serverUrl + "/");
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleLoginReturn(intent);
    }

    /** 브라우저 로그인 후 돌아온 smokingfinder://login?token=... 이면 그 토큰으로 앱 화면에 로그인한다. */
    private boolean handleLoginReturn(Intent intent) {
        Uri data = intent == null ? null : intent.getData();
        if (data == null || !"smokingfinder".equals(data.getScheme()) || !"login".equals(data.getHost())) return false;
        String token = data.getQueryParameter("token");
        if (token == null || token.isEmpty()) return false;
        web.loadUrl(serverUrl + "/auth/app-session?token=" + Uri.encode(token));
        return true;
    }

    private static boolean isSocialLogin(Uri u) {
        String p = u.getPath() == null ? "" : u.getPath();
        return p.matches("^/auth/(google|naver)/login/?$");
    }

    // ---------- 화면 ↔ 앱 연결 ----------
    private class Bridge {
        @JavascriptInterface
        public void requestLocation() {
            // 우리 서버 화면에서 부른 경우에만 응답한다 (다른 사이트가 위치를 얻지 못하게)
            if (!isOurPage(currentUrl)) return;
            main.post(() -> {
                if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
                        || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
                    readLocation();
                } else {
                    requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION,
                            Manifest.permission.ACCESS_COARSE_LOCATION}, REQ_LOCATION);
                }
            });
        }

        @JavascriptInterface
        public String version() {
            return BuildConfig.VERSION_NAME;
        }
    }

    @SuppressWarnings("MissingPermission")
    private void readLocation() {
        LocationManager lm = (LocationManager) getSystemService(LOCATION_SERVICE);
        if (lm == null) { sendLocationError("위치 기능을 쓸 수 없는 기기입니다."); return; }
        boolean gps = lm.isProviderEnabled(LocationManager.GPS_PROVIDER);
        boolean net = lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER);
        if (!gps && !net) { sendLocationError("기기의 위치(GPS) 설정이 꺼져 있습니다."); return; }

        // 2분 안의 최근 위치가 있으면 바로 쓴다
        Location best = null;
        for (String p : new String[]{LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER}) {
            try {
                Location l = lm.getLastKnownLocation(p);
                if (l != null && System.currentTimeMillis() - l.getTime() < 120_000
                        && (best == null || l.getAccuracy() < best.getAccuracy())) best = l;
            } catch (SecurityException ignored) { }
        }
        if (best != null) { sendLocation(best); return; }

        String provider = gps ? LocationManager.GPS_PROVIDER : LocationManager.NETWORK_PROVIDER;
        final boolean[] done = {false};
        CancellationSignal cancel = new CancellationSignal();
        main.postDelayed(() -> {                                   // 15초 안에 못 찾으면 포기
            if (!done[0]) { done[0] = true; cancel.cancel(); sendLocationError("위치를 찾지 못했습니다. 밖으로 나가서 다시 시도하세요."); }
        }, 15_000);
        try {
            if (Build.VERSION.SDK_INT >= 30) {
                lm.getCurrentLocation(provider, cancel, getMainExecutor(), l -> {
                    if (done[0]) return;
                    done[0] = true;
                    if (l != null) sendLocation(l); else sendLocationError("위치를 찾지 못했습니다.");
                });
            } else {
                lm.requestSingleUpdate(provider, new android.location.LocationListener() {
                    @Override public void onLocationChanged(Location l) {
                        if (done[0]) return;
                        done[0] = true;
                        sendLocation(l);
                    }
                    @Override public void onStatusChanged(String p, int st, Bundle b) { }
                    @Override public void onProviderEnabled(String p) { }
                    @Override public void onProviderDisabled(String p) { }
                }, Looper.getMainLooper());
            }
        } catch (SecurityException e) {
            sendLocationError("위치 권한이 없습니다.");
        }
    }

    private void sendLocation(Location l) {
        js(String.format(Locale.US, "window.onNativeLocation && window.onNativeLocation(%.7f, %.7f)", l.getLatitude(), l.getLongitude()));
    }

    private void sendLocationError(String msg) {
        js("window.onNativeLocationError && window.onNativeLocationError(" + jsString(msg) + ")");
    }

    private void js(String code) {
        main.post(() -> web.evaluateJavascript(code, null));
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        boolean granted = false;
        for (int r : results) granted |= r == PackageManager.PERMISSION_GRANTED;
        if (requestCode == REQ_LOCATION) {
            if (geoCallback != null) {                  // 웹 표준 위치 요청(예비 경로)에 대한 응답
                geoCallback.invoke(geoOrigin, granted, false);
                geoCallback = null;
            }
            if (granted) readLocation();
            else sendLocationError("위치 권한이 거부되었습니다. 설정 → 앱 → 흡연장 찾기에서 켤 수 있어요.");
        }
    }

    // ---------- 페이지 이동 ----------
    private class Client extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
            String url = req.getUrl().toString();
            if (isOurPage(url) && isSocialLogin(req.getUrl())) {     // 구글·네이버 로그인은 휴대폰 브라우저로
                openExternal(url + (url.contains("?") ? "&" : "?") + "app=1");
                return true;
            }
            if (isOurPage(url)) return false;            // 우리 서버 화면은 앱 안에서
            openExternal(url);                            // 지도 앱, 거리뷰, 로그인 외부 페이지 등
            return true;
        }

        @Override
        public void onPageStarted(WebView view, String url, Bitmap favicon) {
            currentUrl = url;
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            currentUrl = url;
            CookieManager.getInstance().flush();
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
            if (req.isForMainFrame()) showOfflinePage(String.valueOf(err.getDescription()));
        }
    }

    private boolean isOurPage(String url) {
        if (url == null) return false;
        if (url.startsWith("about:blank") || url.startsWith("data:")) return true;
        return url.equals(serverUrl) || url.startsWith(serverUrl + "/") || url.startsWith(serverUrl + "?");
    }

    private void openExternal(String url) {
        try {
            Intent i = url.startsWith("intent:") ? Intent.parseUri(url, Intent.URI_INTENT_SCHEME) : new Intent(Intent.ACTION_VIEW, Uri.parse(url));
            i.addCategory(Intent.CATEGORY_BROWSABLE);
            i.setComponent(null);
            i.setSelector(null);
            startActivity(i);
        } catch (ActivityNotFoundException e) {
            if (url.startsWith("nmap:")) {
                Toast.makeText(this, "네이버지도 앱이 없어요. 스토어에서 설치할 수 있어요.", Toast.LENGTH_LONG).show();
                openExternal("https://play.google.com/store/apps/details?id=com.nhn.android.nmap");
            } else {
                Toast.makeText(this, "이 링크를 열 앱이 없습니다.", Toast.LENGTH_SHORT).show();
            }
        } catch (Exception e) {
            Toast.makeText(this, "링크를 열 수 없습니다.", Toast.LENGTH_SHORT).show();
        }
    }

    // ---------- 사진 선택, 웹 표준 위치 요청 ----------
    private class Chrome extends WebChromeClient {
        @Override
        public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
            if (fileCallback != null) fileCallback.onReceiveValue(null);
            fileCallback = callback;
            Intent pick = new Intent(Intent.ACTION_GET_CONTENT);
            pick.addCategory(Intent.CATEGORY_OPENABLE);
            pick.setType("image/*");
            try {
                startActivityForResult(Intent.createChooser(pick, "사진 선택"), REQ_FILE);
            } catch (ActivityNotFoundException e) {
                fileCallback = null;
                Toast.makeText(MainActivity.this, "사진을 고를 앱이 없습니다.", Toast.LENGTH_SHORT).show();
                return false;
            }
            return true;
        }

        @Override
        public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
            if (!isOurPage(origin)) { callback.invoke(origin, false, false); return; }
            if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
                callback.invoke(origin, true, false);
            } else {
                geoCallback = callback;
                geoOrigin = origin;
                requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, REQ_LOCATION);
            }
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_FILE && fileCallback != null) {
            Uri[] result = (resultCode == RESULT_OK && data != null && data.getData() != null) ? new Uri[]{data.getData()} : null;
            fileCallback.onReceiveValue(result);
            fileCallback = null;
        }
    }

    // ---------- 서버 연결 실패 화면, 서버 주소 바꾸기 ----------
    private void showOfflinePage(String reason) {
        String html = "<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                + "<style>body{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;"
                + "background:#ffe600;color:#231815;font-family:sans-serif;text-align:center;padding:24px;box-sizing:border-box}"
                + "h1{font-size:1.4rem;margin:0}p{margin:0;opacity:.8}button{font:inherit;font-weight:700;padding:12px 20px;border-radius:999px;"
                + "border:2px solid #231815;background:#231815;color:#fff;min-width:220px}button.sub{background:transparent;color:#231815}</style></head><body>"
                + "<h1>서버에 연결할 수 없어요</h1><p>인터넷 연결을 확인하거나, 서버 주소를 확인해 주세요.</p>"
                + "<p style='font-size:.8rem'>" + escapeHtml(serverUrl) + "<br>" + escapeHtml(reason) + "</p>"
                + "<button onclick='location.replace(" + jsString(serverUrl + "/") + ")'>다시 시도</button>"
                + "<button class='sub' onclick='SmokingAppSettings.openServerDialog()'>서버 주소 바꾸기</button>"
                + "</body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    private class Settings {
        @JavascriptInterface
        public void openServerDialog() {
            main.post(MainActivity.this::showServerDialog);
        }
    }

    private void showServerDialog() {
        EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(serverUrl);
        input.setSelectAllOnFocus(true);
        new AlertDialog.Builder(this)
                .setTitle("서버 주소")
                .setMessage("예: https://smoke.example.com\n같은 와이파이의 PC라면 http://192.168.0.10:9090")
                .setView(input)
                .setPositiveButton("저장", (d, w) -> {
                    String url = trimSlash(input.getText().toString().trim());
                    if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
                    serverUrl = url;
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_SERVER, url).apply();
                    web.loadUrl(serverUrl + "/");
                })
                .setNeutralButton("기본값", (d, w) -> {
                    serverUrl = trimSlash(BuildConfig.SERVER_URL);
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit().remove(KEY_SERVER).apply();
                    web.loadUrl(serverUrl + "/");
                })
                .setNegativeButton("취소", null)
                .show();
    }

    // ---------- 기타 ----------
    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    private static String trimSlash(String s) {
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        return s;
    }

    private static String jsString(String s) {
        StringBuilder b = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c < 0x20 || c == '<' || c == '>' || c == ' ' || c == ' ') b.append(String.format(Locale.US, "\\u%04x", (int) c));
            else b.append(c);
        }
        return b.append('"').toString();
    }

    private static String escapeHtml(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;").replace("\"", "&quot;");
    }
}
