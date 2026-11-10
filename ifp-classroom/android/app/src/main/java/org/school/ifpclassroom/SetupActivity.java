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
import android.view.Gravity;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/** One-time setup on each panel: which classroom this panel is, plus a few options. */
public class SetupActivity extends Activity {
    private Config config;
    private EditText link;
    private EditText pin;
    private Switch autoStart;
    private Switch background;
    private TextView result;

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

        TextView title = text("IFP Classroom — panel setup", 24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(title);
        box.addView(text("On the server PC, open Classrooms, copy this room's kiosk link and enter it below. "
                + "It looks like http://192.168.1.10:8000/kiosk/AbC123…", 15));

        box.addView(label("Kiosk link for this classroom"));
        link = new EditText(this);
        link.setSingleLine(true);
        link.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        link.setHint("http://192.168.1.10:8000/kiosk/…");
        link.setText(config.kioskLink());
        box.addView(link);

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
        pin = new EditText(this);
        pin.setSingleLine(true);
        pin.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        pin.setText(config.pin());
        box.addView(pin);

        LinearLayout buttons = new LinearLayout(this);
        buttons.setPadding(0, dp(20), 0, 0);
        Button test = new Button(this);
        test.setText("Test connection");
        test.setOnClickListener(v -> testConnection());
        Button save = new Button(this);
        save.setText("Save and start");
        save.setOnClickListener(v -> saveAndStart());
        buttons.addView(test);
        buttons.addView(save);
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

    private void testConnection() {
        final String[] p = Config.parse(link.getText().toString());
        if (p == null) {
            result.setTextColor(Color.rgb(198, 40, 40));
            result.setText("That is not a kiosk link. It must contain /kiosk/ followed by the room's code.");
            return;
        }
        result.setTextColor(Color.DKGRAY);
        result.setText("Connecting…");
        new Thread(() -> {
            String message;
            boolean ok = false;
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(Config.rosterUrl(p)).openConnection();
                c.setConnectTimeout(8000);
                c.setReadTimeout(15000);
                int code = c.getResponseCode();
                if (code == 404) {
                    message = "The server answered, but this kiosk link is not valid (wrong code, room deleted, "
                            + "disabled, or a new link was made). Copy the link again from the Classrooms page.";
                } else if (code != 200) {
                    message = "The server answered with error " + code + ".";
                } else {
                    JSONObject o = new JSONObject(read(c.getInputStream()));
                    message = "Connected ✓  Classroom: " + o.getJSONObject("classroom").getString("name")
                            + " · " + o.getJSONArray("persons").length() + " people with face photos.";
                    ok = true;
                }
            } catch (Exception e) {
                message = "Cannot reach the server: " + e.getMessage() + "\nCheck that the server PC is on, "
                        + "the address is right, the panel is on the same network, and port 8000 is open in "
                        + "the Windows firewall (the server installer does this).";
            }
            final String m = message;
            final boolean success = ok;
            runOnUiThread(() -> {
                result.setTextColor(success ? Color.rgb(31, 143, 78) : Color.rgb(198, 40, 40));
                result.setText(m);
            });
        }).start();
    }

    private void saveAndStart() {
        if (Config.parse(link.getText().toString()) == null) {
            result.setTextColor(Color.rgb(198, 40, 40));
            result.setText("Enter this classroom's kiosk link first.");
            return;
        }
        config.save(link.getText().toString(), autoStart.isChecked(), background.isChecked(),
                pin.getText().toString());
        startActivity(new Intent(this, KioskActivity.class).putExtra("reload", true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP));
        finish();
    }

    private static String read(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        return out.toString("UTF-8");
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
        t.setGravity(Gravity.START);
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
