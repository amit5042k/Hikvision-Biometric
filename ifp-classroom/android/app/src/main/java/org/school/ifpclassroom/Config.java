package org.school.ifpclassroom;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;

import org.json.JSONException;
import org.json.JSONObject;

/** App settings: the classroom's kiosk link and a few switches. */
final class Config {
    private static final String PREFS = "ifp_classroom";
    private final SharedPreferences prefs;

    Config(Context context) {
        prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    String kioskLink() { return prefs.getString("kiosk_link", ""); }
    boolean autoStart() { return prefs.getBoolean("auto_start", true); }
    boolean background() { return prefs.getBoolean("background", true); }
    String pin() { return prefs.getString("pin", ""); }

    void save(String link, boolean autoStart, boolean background, String pin) {
        prefs.edit().putString("kiosk_link", link.trim()).putBoolean("auto_start", autoStart)
                .putBoolean("background", background).putString("pin", pin.trim()).apply();
    }

    boolean isConfigured() { return parse(kioskLink()) != null; }

    /** Server base URL and token from a link like http://192.168.1.10:8000/kiosk/AbC123. */
    static String[] parse(String link) {
        if (link == null) return null;
        link = link.trim();
        if (!link.startsWith("http://") && !link.startsWith("https://")) link = "http://" + link;
        Uri uri = Uri.parse(link);
        if (uri.getHost() == null || uri.getHost().isEmpty()) return null;
        java.util.List<String> parts = uri.getPathSegments();
        int i = parts.indexOf("kiosk");
        if (i < 0 || i + 1 >= parts.size() || parts.get(i + 1).isEmpty()) return null;
        String base = uri.getScheme() + "://" + uri.getEncodedAuthority();
        // keep any path prefix before /kiosk/ (server behind a sub-path)
        StringBuilder prefix = new StringBuilder();
        for (int j = 0; j < i; j++) prefix.append('/').append(parts.get(j));
        return new String[]{base + prefix, parts.get(i + 1)};
    }

    static String rosterUrl(String[] p) { return p[0] + "/api/class/" + Uri.encode(p[1]) + "/roster"; }

    /** The object the kiosk page reads as window.KIOSK. */
    String kioskJson() {
        String[] p = parse(kioskLink());
        JSONObject o = new JSONObject();
        try {
            if (p != null) {
                o.put("roster", rosterUrl(p));
                o.put("sightings", p[0] + "/api/class/" + Uri.encode(p[1]) + "/sightings");
                o.put("key", "kiosk-app-" + Integer.toHexString((p[0] + p[1]).hashCode()));
            }
        } catch (JSONException ignored) {
            // cannot happen with string values
        }
        return o.toString();
    }
}
