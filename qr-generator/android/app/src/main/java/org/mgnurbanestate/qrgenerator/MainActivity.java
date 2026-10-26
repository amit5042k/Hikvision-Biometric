package org.mgnurbanestate.qrgenerator;

import android.Manifest;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Insets;
import android.media.MediaScannerConnection;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import android.view.WindowInsets;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebResourceError;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import androidx.core.content.FileProvider;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;

/**
 * Hosts the QR Generator website from your server (BuildConfig.SERVER_URL) in a WebView
 * and gives it native save/share through the {@code AndroidBridge} JavaScript object.
 * Accounts, the 7-day trial and the QR codes themselves all live on the server.
 */
public class MainActivity extends Activity {

    private static final String START_URL = BuildConfig.SERVER_URL + "/";
    private static final String OFFLINE_URL = "file:///android_asset/offline.html";
    private static final Uri SERVER = Uri.parse(BuildConfig.SERVER_URL);
    private static final String FOLDER = "QR Generator";
    private static final int REQ_FILE = 1;
    private static final int REQ_STORAGE = 2;

    private WebView web;
    private ValueCallback<Uri[]> fileCallback;
    private String pendingSharedText;
    private boolean pageReady;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        web = new WebView(this);
        web.setBackgroundColor(0); // let the themed window background show while loading
        setContentView(web);
        applySystemBarInsets();

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowContentAccess(true);

        web.addJavascriptInterface(new Bridge(), "AndroidBridge");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                // Stay in the app for our own pages; short links (/r/...) and everything else
                // open in the browser, exactly as they would for someone scanning the code.
                boolean ours = SERVER.getScheme().equals(uri.getScheme()) && SERVER.getHost().equals(uri.getHost())
                        && SERVER.getPort() == uri.getPort();
                String path = uri.getPath() == null ? "" : uri.getPath();
                if (ours && !path.startsWith("/r/")) return false;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                } catch (ActivityNotFoundException ignored) {
                    // nothing can open it; stay on the page
                }
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) view.loadUrl(OFFLINE_URL);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                pageReady = !OFFLINE_URL.equals(url);
                deliverSharedText();
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                try {
                    startActivityForResult(params.createIntent(), REQ_FILE);
                } catch (ActivityNotFoundException e) {
                    fileCallback = null;
                    toast("No app available to pick an image");
                    return false;
                }
                return true;
            }
        });

        handleIntent(getIntent());
        if (savedInstanceState == null || web.restoreState(savedInstanceState) == null) {
            web.loadUrl(START_URL);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handleIntent(intent);
        deliverSharedText();
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (web.canGoBack() && !OFFLINE_URL.equals(web.getUrl())) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    protected void onDestroy() {
        web.destroy();
        super.onDestroy();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_FILE && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data));
            fileCallback = null;
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        if (requestCode == REQ_STORAGE && results.length > 0
                && results[0] == PackageManager.PERMISSION_GRANTED) {
            toast("Storage access allowed — tap Download again");
        }
    }

    private void handleIntent(Intent intent) {
        if (intent != null && Intent.ACTION_SEND.equals(intent.getAction())) {
            pendingSharedText = intent.getStringExtra(Intent.EXTRA_TEXT);
        }
    }

    private void deliverSharedText() {
        if (!pageReady || pendingSharedText == null || OFFLINE_URL.equals(web.getUrl())) return;
        web.evaluateJavascript("window.QRGen && QRGen.fill(" + JSONObject.quote(pendingSharedText) + ")", null);
        pendingSharedText = null;
    }

    /** Android 15 draws apps edge-to-edge; pad the WebView so content clears the system bars and keyboard. */
    private void applySystemBarInsets() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return; // older versions fit system windows already
        getWindow().setDecorFitsSystemWindows(false);
        web.setOnApplyWindowInsetsListener((v, insets) -> {
            Insets i = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
            v.setPadding(i.left, i.top, i.right, i.bottom);
            return WindowInsets.CONSUMED;
        });
    }

    private void toast(String msg) {
        runOnUiThread(() -> Toast.makeText(this, msg, Toast.LENGTH_SHORT).show());
    }

    /** Methods callable from app.js as window.AndroidBridge.*. They run on a WebView background thread. */
    private class Bridge {

        /** Called by the offline page's Retry button. */
        @JavascriptInterface
        public void retry() {
            runOnUiThread(() -> {
                pageReady = false;
                web.loadUrl(START_URL);
            });
        }

        @JavascriptInterface
        public void saveFile(String base64, String mime, String name) {
            byte[] bytes = Base64.decode(base64, Base64.DEFAULT);
            boolean image = "image/png".equals(mime);
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    saveWithMediaStore(bytes, mime, name, image);
                } else if (!saveLegacy(bytes, mime, name, image)) {
                    return;
                }
                toast("Saved to " + (image ? "Pictures/" : "Download/") + FOLDER);
            } catch (IOException | RuntimeException e) {
                toast("Could not save: " + e.getMessage());
            }
        }

        @JavascriptInterface
        public void shareImage(String base64, String name) {
            try {
                File dir = new File(getCacheDir(), "shared");
                if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("cache unavailable");
                File file = new File(dir, name);
                try (OutputStream out = new FileOutputStream(file)) {
                    out.write(Base64.decode(base64, Base64.DEFAULT));
                }
                Uri uri = FileProvider.getUriForFile(MainActivity.this, getPackageName() + ".fileprovider", file);
                Intent send = new Intent(Intent.ACTION_SEND)
                        .setType("image/png")
                        .putExtra(Intent.EXTRA_STREAM, uri)
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                runOnUiThread(() -> startActivity(Intent.createChooser(send, "Share QR code")));
            } catch (IOException | RuntimeException e) {
                toast("Could not share: " + e.getMessage());
            }
        }

        private void saveWithMediaStore(byte[] bytes, String mime, String name, boolean image) throws IOException {
            ContentResolver resolver = getContentResolver();
            ContentValues values = new ContentValues();
            values.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
            values.put(MediaStore.MediaColumns.MIME_TYPE, mime);
            values.put(MediaStore.MediaColumns.RELATIVE_PATH,
                    (image ? Environment.DIRECTORY_PICTURES : Environment.DIRECTORY_DOWNLOADS) + "/" + FOLDER);
            values.put(MediaStore.MediaColumns.IS_PENDING, 1);
            Uri collection = image
                    ? MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
                    : MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY);
            Uri item = resolver.insert(collection, values);
            if (item == null) throw new IOException("storage unavailable");
            try (OutputStream out = resolver.openOutputStream(item)) {
                if (out == null) throw new IOException("storage unavailable");
                out.write(bytes);
            } catch (IOException e) {
                resolver.delete(item, null, null);
                throw e;
            }
            values.clear();
            values.put(MediaStore.MediaColumns.IS_PENDING, 0);
            resolver.update(item, values, null, null);
        }

        /** Android 8–9: write to public storage; returns false if permission still has to be granted. */
        private boolean saveLegacy(byte[] bytes, String mime, String name, boolean image) throws IOException {
            if (checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
                runOnUiThread(() -> requestPermissions(
                        new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE));
                return false;
            }
            File dir = new File(Environment.getExternalStoragePublicDirectory(
                    image ? Environment.DIRECTORY_PICTURES : Environment.DIRECTORY_DOWNLOADS), FOLDER);
            if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("cannot create " + dir);
            File file = new File(dir, name);
            int dot = name.lastIndexOf('.');
            for (int n = 1; file.exists(); n++) {
                file = new File(dir, name.substring(0, dot) + " (" + n + ")" + name.substring(dot));
            }
            try (OutputStream out = new FileOutputStream(file)) {
                out.write(bytes);
            }
            MediaScannerConnection.scanFile(MainActivity.this, new String[]{file.getPath()}, new String[]{mime}, null);
            return true;
        }
    }
}
