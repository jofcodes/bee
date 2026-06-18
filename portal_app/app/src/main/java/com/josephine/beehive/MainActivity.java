package com.josephine.beehive;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * Beehive Monitor — a full-screen, kiosk-style WebView that renders the
 * self-contained dashboard (threats + most-active clips + fullscreen/ambient
 * playback). All content is local; no network is used.
 *
 * Dashboard source resolution:
 *   1. {externalFilesDir}/dashboard/index.html  — a pushed "quick refresh" copy
 *   2. otherwise, the bundled asset is copied there and loaded
 *   3. last resort: file:///android_asset/dashboard/index.html
 */
public class MainActivity extends Activity {

    private static final String DASH = "dashboard/index.html";

    private FrameLayout root;
    private WebView web;
    private View customView;                                   // HTML5 fullscreen video
    private WebChromeClient.CustomViewCallback customCallback;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        hideSystemBars();

        root = new FrameLayout(this);
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));
        setContentView(root);

        WebSettings ws = web.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setMediaPlaybackRequiresUserGesture(false);         // ambient autoplay
        ws.setDomStorageEnabled(true);
        ws.setAllowFileAccess(true);
        ws.setAllowFileAccessFromFileURLs(true);
        ws.setAllowUniversalAccessFromFileURLs(true);
        ws.setLoadWithOverviewMode(true);
        ws.setUseWideViewPort(true);
        ws.setCacheMode(WebSettings.LOAD_NO_CACHE);

        web.setWebViewClient(new WebViewClient());             // keep navigation in-app
        web.setWebChromeClient(new FullscreenChrome());

        web.loadUrl(resolveDashboardUrl());
    }

    private String resolveDashboardUrl() {
        File ext = new File(getExternalFilesDir(null), DASH);
        if (ext.exists()) {
            return "file://" + ext.getAbsolutePath();
        }
        // Copy the bundled asset out so a later `adb push` to this same path can
        // override it without reinstalling the APK.
        try {
            ext.getParentFile().mkdirs();
            InputStream in = getAssets().open(DASH);
            OutputStream os = new FileOutputStream(ext);
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) os.write(buf, 0, n);
            os.close();
            in.close();
            return "file://" + ext.getAbsolutePath();
        } catch (Exception e) {
            return "file:///android_asset/" + DASH;            // last resort
        }
    }

    private void hideSystemBars() {
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemBars();
    }

    @Override
    public void onBackPressed() {
        if (customView != null) {                              // exit fullscreen video
            if (customCallback != null) {
                try { customCallback.onCustomViewHidden(); } catch (Exception ignored) {}
            }
            return;
        }
        if (web.canGoBack()) {
            web.goBack();
            return;
        }
        super.onBackPressed();
    }

    /** Bridges the dashboard's requestFullscreen() to a real fullscreen surface. */
    private class FullscreenChrome extends WebChromeClient {
        @Override
        public void onShowCustomView(View view, CustomViewCallback cb) {
            if (customView != null) { cb.onCustomViewHidden(); return; }
            customView = view;
            customCallback = cb;
            root.addView(customView, new FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.MATCH_PARENT));
            web.setVisibility(View.GONE);
            hideSystemBars();
        }

        @Override
        public void onHideCustomView() {
            if (customView == null) return;
            root.removeView(customView);
            customView = null;
            customCallback = null;
            web.setVisibility(View.VISIBLE);
            hideSystemBars();
        }
    }

    @Override protected void onPause() { super.onPause(); web.onPause(); }
    @Override protected void onResume() { super.onResume(); web.onResume(); hideSystemBars(); }
    @Override protected void onDestroy() { if (web != null) web.destroy(); super.onDestroy(); }
}
