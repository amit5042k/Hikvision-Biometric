package org.school.ifpclassroom;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Opens the classroom camera when the panel is switched on. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        Config config = new Config(context);
        if (!config.isConfigured() || !config.autoStart()) return;
        if (config.background()) KeepAliveService.start(context);
        Intent open = new Intent(context, KioskActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            // Android 10+ allows this when "Display over other apps" is granted (see the setup screen).
            context.startActivity(open);
        } catch (RuntimeException ignored) {
            // blocked by the system; the teacher can open the app from the home screen
        }
    }
}
