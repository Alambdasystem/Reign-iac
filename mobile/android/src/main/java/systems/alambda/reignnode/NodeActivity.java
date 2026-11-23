package systems.alambda.reignnode;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.webkit.*;
import android.widget.*;
import java.io.*;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONObject;

public class NodeActivity extends Activity {
    private static final String AX = "https://reign-ax-pilot-d934.onrender.com";
    private static final String ASSETS = "https://appassets.androidplatform.net";
    private static final String MODEL = "SmolLM2-360M-Instruct-Q4_K_M";
    private final ExecutorService inventory = Executors.newSingleThreadExecutor();
    private final ExecutorService chat = Executors.newSingleThreadExecutor();
    private WebView phone, console;
    private LinearLayout shell;
    private FrameLayout screens;
    private Button back;
    private SharedPreferences store;
    private volatile boolean foreground;
    private String threadId;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        store = getSharedPreferences("private-node", MODE_PRIVATE);
        shell = new LinearLayout(this);
        shell.setOrientation(LinearLayout.VERTICAL);
        shell.setBackgroundColor(Color.rgb(10, 17, 25));
        shell.setOnApplyWindowInsetsListener((v, insets) -> {
            v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        back = new Button(this);
        back.setText("Return to REIGN Node");
        back.setOnClickListener(v -> closeConsole());
        back.setVisibility(View.GONE);
        shell.addView(back);
        screens = new FrameLayout(this);
        shell.addView(screens, new LinearLayout.LayoutParams(-1, 0, 1));
        phone = new WebView(this);
        configure(phone);
        phone.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) { return true; }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
                Uri url = req.getUrl();
                if (ASSETS.equals(url.getScheme() + "://" + url.getAuthority()) && "GET".equals(req.getMethod())) {
                    String path = url.getPath();
                    if (path != null && path.matches("/[a-z0-9.-]+")) {
                        String name = path.substring(1);
                        String mime = name.endsWith(".js") ? "application/javascript"
                            : name.endsWith(".html") ? "text/html" : name.endsWith(".css") ? "text/css"
                            : name.endsWith(".wasm") ? "application/wasm" : "application/octet-stream";
                        try {
                            return new WebResourceResponse(mime, null, 200, "OK",
                                Collections.singletonMap("Cache-Control","no-store"), getAssets().open(name));
                        } catch (IOException ignored) { }
                    }
                }
                return new WebResourceResponse("text/plain", "UTF-8", 404, "Not Found",
                    Collections.emptyMap(), new ByteArrayInputStream(new byte[0]));
            }
        });
        phone.addJavascriptInterface(new PhoneBridge(), "ReignPhone");
        screens.addView(phone);
        setContentView(shell);
        phone.loadUrl(ASSETS + "/index.html");
    }

    private void configure(WebView view) {
        view.getSettings().setJavaScriptEnabled(true);
        view.getSettings().setDomStorageEnabled(true);
        view.getSettings().setAllowFileAccess(false);
        view.getSettings().setAllowContentAccess(false);
        view.getSettings().setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        CookieManager.getInstance().setAcceptThirdPartyCookies(view, false);
    }

    private void showConsole() {
        if (console != null) return;
        console = new WebView(this);
        configure(console);
        console.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri url = req.getUrl();
                return !(AX.equals(url.getScheme() + "://" + url.getAuthority()));
            }
        });
        screens.addView(console);
        back.setVisibility(View.VISIBLE);
        console.loadUrl(AX + "/nodes");
    }

    private void closeConsole() {
        if (console == null) return;
        screens.removeView(console);
        console.destroy();
        console = null;
        back.setVisibility(View.GONE);
        CookieManager.getInstance().flush();
    }

    @Override public void onBackPressed() {
        if (console != null) closeConsole(); else super.onBackPressed();
    }
    @Override protected void onResume() { super.onResume(); foreground = true; }
    @Override protected void onPause() { foreground = false; super.onPause(); }
    @Override protected void onDestroy() {
        inventory.shutdownNow(); chat.shutdownNow();
        closeConsole();
        phone.removeJavascriptInterface("ReignPhone");
        screens.removeView(phone); phone.destroy();
        super.onDestroy();
    }

    private static JSONObject fields(Object... items) {
        JSONObject json = new JSONObject();
        try {
            for (int i = 0; i < items.length; i += 2) json.put((String)items[i], items[i+1]);
        } catch (org.json.JSONException ex) { throw new IllegalArgumentException(ex); }
        return json;
    }
    private void emit(String event, JSONObject data) {
        runOnUiThread(() -> {
            if (isDestroyed() || isFinishing()) return;
            phone.evaluateJavascript("window.nodeEvent?.(" + JSONObject.quote(event) + "," + data + ")", null);
        });
    }

    private static final class ApiError extends IOException {
        final int code;
        ApiError(int code) { super("HTTP " + code); this.code = code; }
    }

    private JSONObject call(String path, JSONObject body, String nodeToken) throws Exception {
        HttpsURLConnection con = (HttpsURLConnection)new URL(AX + path).openConnection();
        try {
            con.setInstanceFollowRedirects(false);
            con.setConnectTimeout(10000); con.setReadTimeout(20000); con.setUseCaches(false);
            if (nodeToken != null) {
                con.setRequestProperty("X-Node-Token", nodeToken);
            } else {
                String cookies = CookieManager.getInstance().getCookie(AX);
                if (cookies == null) throw new ApiError(401);
                con.setRequestProperty("Cookie", cookies);
                con.setRequestProperty("Origin", AX);
                for (String part : cookies.split(";")) {
                    String value = part.trim();
                    if (value.startsWith("ax_csrf=")) con.setRequestProperty("X-CSRF-Token", value.substring(8));
                }
            }
            if (body != null) {
                con.setRequestMethod("POST");
                con.setDoOutput(true); con.setRequestProperty("Content-Type", "application/json");
                try (OutputStream out = con.getOutputStream()) { out.write(body.toString().getBytes(StandardCharsets.UTF_8)); }
            }
            int code = con.getResponseCode();
            if (code != 200 && code != 201) throw new ApiError(code);
            try (InputStream in = con.getInputStream(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
                byte[] buf = new byte[4096];
                for (int count; (count = in.read(buf)) != -1;) {
                    if (out.size() + count > 1048576) throw new IOException("Response limit");
                    out.write(buf, 0, count);
                }
                return new JSONObject(out.toString("UTF-8"));
            }
        } finally { con.disconnect(); }
    }

    private static String explain(Exception e) {
        if (e instanceof ApiError && (((ApiError)e).code == 401 || ((ApiError)e).code == 403))
            return "Open Genesis, sign in with your REIGN account, then return to this app.";
        return "REIGN couldn’t complete this request. Check the connection and try again.";
    }
    private String nodeId() { return store.getString("id", ""); }
    private void pairState(String message) {
        emit("pair", fields("paired", store.contains("token"), "message", message));
    }

    public final class PhoneBridge {
        @JavascriptInterface public void status() {
            pairState(store.contains("token") ? "Registered as " + nodeId() + ". Load the model to check in."
                                             : "Sign in to REIGN, then register this phone.");
        }
        @JavascriptInterface public void openGenesis() { runOnUiThread(() -> showConsole()); }
        @JavascriptInterface public void register() {
            inventory.execute(() -> {
                if (store.contains("token")) { status(); return; }
                String id = nodeId();
                if (id.isEmpty()) {
                    id = "android-" + UUID.randomUUID();
                    if (!store.edit().putString("id", id).commit()) { pairState("Couldn’t store the phone identity."); return; }
                }
                try {
                    org.json.JSONArray caps = new org.json.JSONArray().put("model.inference");
                    JSONObject receipt = call("/api/nodes/enroll", fields("node_id", id, "kind", "edge",
                        "label", "REIGN Node · Android · " + MODEL, "capabilities", caps), null);
                    String token = receipt.getString("enrollment_token");
                    if (!id.equals(receipt.getString("node_id")) || !token.matches("[a-f0-9]{64}"))
                        throw new IOException("Invalid enrollment response");
                    if (!store.edit().putString("token", token).commit()) throw new IOException("Storage unavailable");
                    pairState("Registered as " + id + ".");
                } catch (Exception e) { pairState(explain(e)); }
            });
        }
        @JavascriptInterface public void heartbeat() {
            if (!foreground || !store.contains("token")) return;
            inventory.execute(() -> {
                if (!foreground) return;
                String status;
                try {
                    JSONObject app = fields("app_id","systems.alambda.reignnode","name","REIGN Node",
                        "version","0.1.0","model", MODEL, "runtime","llama.cpp / wllama 2.3.7");
                    JSONObject reply = call("/api/nodes/" + nodeId() + "/heartbeat",
                        fields("apps", new org.json.JSONArray().put(app)), store.getString("token",""));
                    if (!reply.optBoolean("accepted") || !reply.optBoolean("inventory_updated")) throw new IOException();
                    status = "Online in Genesis · " + nodeId();
                } catch (ApiError e) {
                    status = e.code == 404 || e.code == 405 ? "Registered. This server hasn’t enabled phone check-ins yet."
                        : e.code == 401 ? "Check-in denied. Check this node’s enrollment in Genesis."
                        : "Check-in unavailable. The local model still works.";
                } catch (Exception e) { status = "Check-in unavailable. The local model still works."; }
                emit("connection", fields("message", status));
            });
        }
        @JavascriptInterface public void askCore(int id, String message) {
            if (message == null || message.trim().isEmpty() || message.length() > 4000) {
                emit("core", fields("id",id,"success",false,"message","Enter a message of up to 4,000 characters."));
                return;
            }
            chat.execute(() -> {
                try {
                    JSONObject state = call("/api/chat", fields("message",message,"thread_id",threadId), null);
                    threadId = UUID.fromString(state.getString("thread_id")).toString();
                    long deadline = android.os.SystemClock.elapsedRealtime() + 120000;
                    while ("running".equals(state.optString("status")) && android.os.SystemClock.elapsedRealtime() < deadline) {
                        Thread.sleep(850);
                        state = call("/api/chat/" + threadId, null, null);
                    }
                    String answer = state.optString("answer", "");
                    if ("completed".equals(state.optString("status")) && !answer.isEmpty()) {
                        emit("core", fields("id",id,"success",true,"message",answer));
                    } else {
                        emit("core", fields("id",id,"success",false,"message","Core reports " + state.optString("status")
                            + ". Open REIGN for the run details."));
                    }
                    if (!state.optBoolean("continuable",true)) threadId = null;
                } catch (Exception e) {
                    if (e instanceof ApiError && ((ApiError)e).code == 404) threadId = null;
                    emit("core", fields("id",id,"success",false,"message",explain(e)));
                }
            });
        }
    }
}
