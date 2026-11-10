package org.school.ifpclassroom;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.webkit.WebViewAssetLoader;

/**
 * Full-screen classroom camera. The kiosk page is served from inside the app at
 * https://appassets.androidplatform.net, a secure address, so the camera works
 * without HTTPS on the school server. The page talks to the server directly.
 */
public class KioskActivity extends Activity {
    private static final int REQ_CAMERA = 1;
    private static final String PAGE = "https://appassets.androidplatform.net/assets/www/index.html";

    private WebView web;
    private Config config;
    private boolean loaded;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        config = new Config(this);
        if (!config.isConfigured()) {
            startActivity(new Intent(this, SetupActivity.class));
            finish();
            return;
        }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        }
        web = new WebView(this);
        setContentView(web);
        hideSystemBars();
        setUpWebView();
        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            start();
        } else {
            requestPermissions(new String[]{Manifest.permission.CAMERA}, REQ_CAMERA);
        }
    }

    private void setUpWebView() {
        final WebViewAssetLoader assets = new WebViewAssetLoader.Builder()
                .addPathHandler("/assets/", new WebViewAssetLoader.AssetsPathHandler(this)).build();
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        // the page is https (in-app) and the school server is usually http
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        if (Build.VERSION.SDK_INT >= 26) {
            // keep the page running at full priority even when another app is in front
            web.setRendererPriorityPolicy(WebView.RENDERER_PRIORITY_IMPORTANT, false);
        }
        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                return assets.shouldInterceptRequest(request.getUrl());
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return !"appassets.androidplatform.net".equals(request.getUrl().getHost());
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                Uri origin = request.getOrigin();
                boolean ours = origin != null && "appassets.androidplatform.net".equals(origin.getHost());
                boolean wantsCameraOnly = true;
                for (String r : request.getResources()) {
                    if (!PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(r)) wantsCameraOnly = false;
                }
                if (ours && wantsCameraOnly) {
                    request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
                } else {
                    request.deny();
                }
            }
        });
        web.addJavascriptInterface(new Bridge(), "AndroidKiosk");
    }

    private void start() {
        if (loaded) return;
        loaded = true;
        if (config.background()) KeepAliveService.start(this);
        web.loadUrl(PAGE);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == REQ_CAMERA) {
            // load the page either way: without permission it explains what to do
            start();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        hideSystemBars();
        if (web != null) {
            web.onResume();
            web.resumeTimers();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        // In background mode the page keeps recognising while another app is in front.
        if (web != null && !config.background()) web.onPause();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (intent.getBooleanExtra("reload", false) && web != null) {
            config = new Config(this);
            if (config.background()) KeepAliveService.start(this); else KeepAliveService.stop(this);
            web.loadUrl(PAGE);
        }
    }

    @Override
    public void onBackPressed() {
        // Leave the kiosk running and go back to the launcher / previous app.
        moveTaskToBack(true);
    }

    @Override
    protected void onDestroy() {
        if (web != null) web.destroy();
        super.onDestroy();
    }

    private void hideSystemBars() {
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
    }

    /** Methods the kiosk page can call as window.AndroidKiosk.*. */
    private class Bridge {
        @JavascriptInterface
        public String config() {
            return new Config(KioskActivity.this).kioskJson();
        }

        @JavascriptInterface
        public void openSettings() {
            runOnUiThread(() -> startActivity(new Intent(KioskActivity.this, SetupActivity.class)
                    .putExtra("from_kiosk", true)));
        }
    }
}
