package org.school.ifpclassroom;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * Panel setup: sign in to the school server with this classroom's own login.
 * The server answers with the classroom's kiosk link; the password is not stored.
 */
public class SetupActivity extends Activity {
    private Config config;
    private EditText server;
    private EditText username;
    private EditText password;
    private EditText pin;
    private Switch autoStart;
    private Switch background;
    private TextView result;
    private Button signIn;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        config = new Config(this);
        buildUi();
        if (getIntent().getBooleanExtra("from_kiosk", false) && !config.pin().isEmpty()) askPin();
    }

    private void buildUi() {
        int pad = dp(24);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(pad, pad, pad, pad);

        TextView title = text("IFP Classroom — sign in this panel", 24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(title);
        if (config.isConfigured()) {
            box.addView(text("Currently signed in as " + config.username()
                    + (config.classroomName().isEmpty() ? "" : " (" + config.classroomName() + ")") + ".", 15));
        }
        box.addView(text("Use this classroom's own login. The administrator creates it on the server's "
                + "Classrooms page (Manage classrooms → Create login).", 15));

        box.addView(label("Server address"));
        server = field("e.g. 192.168.1.10:8000", InputType.TYPE_TEXT_VARIATION_URI);
        server.setText(config.server());
        box.addView(server);

        box.addView(label("Classroom username"));
        username = field("e.g. class8a", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        username.setText(config.username());
        box.addView(username);

        box.addView(label("Password"));
        password = field("", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        box.addView(password);

        autoStart = new Switch(this);
        autoStart.setText("Open automatically when the panel is switched on");
        autoStart.setChecked(config.autoStart());
        autoStart.setPadding(0, dp(16), 0, 0);
        box.addView(autoStart);

        background = new Switch(this);
        background.setText("Keep recognising while another app is on screen (works on many panels; test it)");
        background.setChecked(config.background());
        background.setPadding(0, dp(12), 0, 0);
        box.addView(background);

        box.addView(label("Settings PIN (optional — stops students changing these settings)"));
        pin = field("", InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        pin.setText(config.pin());
        box.addView(pin);

        LinearLayout buttons = new LinearLayout(this);
        buttons.setPadding(0, dp(20), 0, 0);
        signIn = new Button(this);
        signIn.setText("Sign in and start");
        signIn.setOnClickListener(v -> signIn());
        buttons.addView(signIn);
        if (config.isConfigured()) {
            Button keep = new Button(this);
            keep.setText("Save options only");
            keep.setOnClickListener(v -> saveOptionsOnly());
            buttons.addView(keep);
        }
        box.addView(buttons);

        result = text("", 15);
        result.setPadding(0, dp(12), 0, 0);
        box.addView(result);

        if (Build.VERSION.SDK_INT >= 29) {
            box.addView(label("Automatic start on Android 10 and later"));
            box.addView(text("Android only lets an app open itself at power-on when it may "
                    + "\"Display over other apps\". Many panels also have their own \"start-up app\" setting.", 14));
            Button overlay = new Button(this);
            overlay.setText(Settings.canDrawOverlays(this) ? "Display over other apps: allowed ✓"
                    : "Allow \"Display over other apps\"");
            overlay.setOnClickListener(v -> {
                try {
                    startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                            Uri.parse("package:" + getPackageName())));
                } catch (RuntimeException e) {
                    result.setText("This panel has no such setting. Use the panel's own start-up app setting.");
                }
            });
            box.addView(overlay);
        }

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.WHITE);
        scroll.addView(box);
        setContentView(scroll);
    }

    private void askPin() {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        new AlertDialog.Builder(this).setTitle("Enter the settings PIN").setView(input).setCancelable(false)
                .setPositiveButton("OK", (d, w) -> {
                    if (!input.getText().toString().equals(config.pin())) finish();
                })
                .setNegativeButton("Cancel", (d, w) -> finish()).show();
    }

    private void signIn() {
        final String base = Config.normaliseServer(server.getText().toString());
        final String user = username.getText().toString().trim();
        final String pass = password.getText().toString();
        if (base.isEmpty() || user.isEmpty() || pass.isEmpty()) {
            error("Enter the server address, the classroom username and its password.");
            return;
        }
        signIn.setEnabled(false);
        result.setTextColor(Color.DKGRAY);
        result.setText("Signing in to " + base + " …");
        new Thread(() -> {
            String message = null;
            String link = null;
            String room = "";
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(base + "/api/panel/login").openConnection();
                c.setConnectTimeout(8000);
                c.setReadTimeout(15000);
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                JSONObject body = new JSONObject().put("username", user).put("password", pass);
                try (OutputStream out = c.getOutputStream()) {
                    out.write(body.toString().getBytes(StandardCharsets.UTF_8));
                }
                int code = c.getResponseCode();
                InputStream in = code < 400 ? c.getInputStream() : c.getErrorStream();
                JSONObject o;
                try {
                    o = new JSONObject(read(in));
                } catch (Exception notJson) {
                    o = new JSONObject();
                }
                if (code == 200 && o.optBoolean("ok")) {
                    link = base + o.getString("kiosk_path");
                    room = o.getJSONObject("classroom").getString("name");
                } else if (code == 404) {
                    message = "That address answered, but it is not the IFP Classroom server. Check the address "
                            + "and port (usually 8000).";
                } else {
                    message = o.optString("error", "The server answered with error " + code + ".");
                }
            } catch (Exception e) {
                message = "Cannot reach the server at " + base + " (" + e.getMessage() + ").\nCheck that the "
                        + "server PC is on, the address is right, the panel is on the school network, and port "
                        + "8000 is open in the Windows firewall (the server installer does this).";
            }
            final String err = message;
            final String kiosk = link;
            final String roomName = room;
            runOnUiThread(() -> {
                signIn.setEnabled(true);
                if (err != null) {
                    error(err);
                    return;
                }
                config.save(base, user, roomName, kiosk, autoStart.isChecked(), background.isChecked(),
                        pin.getText().toString());
                startKiosk();
            });
        }).start();
    }

    private void saveOptionsOnly() {
        config.save(config.server(), config.username(), config.classroomName(), config.kioskLink(),
                autoStart.isChecked(), background.isChecked(), pin.getText().toString());
        startKiosk();
    }

    private void startKiosk() {
        startActivity(new Intent(this, KioskActivity.class).putExtra("reload", true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP));
        finish();
    }

    private void error(String message) {
        result.setTextColor(Color.rgb(198, 40, 40));
        result.setText(message);
    }

    private static String read(InputStream in) throws java.io.IOException {
        if (in == null) return "";
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        return out.toString("UTF-8");
    }

    private EditText field(String hint, int inputType) {
        EditText e = new EditText(this);
        e.setSingleLine(true);
        e.setInputType(inputType);
        e.setHint(hint);
        return e;
    }

    private TextView text(String s, int sp) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(Color.rgb(29, 36, 51));
        t.setPadding(0, dp(6), 0, 0);
        return t;
    }

    private TextView label(String s) {
        TextView t = text(s, 14);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setPadding(0, dp(18), 0, 0);
        return t;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    @Override
    public void onBackPressed() {
        if (config.isConfigured()) super.onBackPressed();
        // first-time setup: nothing to go back to
    }
}
