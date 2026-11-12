package org.school.ifpclassroom;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import androidx.core.app.ServiceCompat;

/**
 * Foreground service that tells Android the classroom camera is in use, so the
 * app is not stopped and (on Android 11+) may keep using the camera while
 * another app is in front. How well this works depends on the panel's
 * Android version and manufacturer.
 */
public class KeepAliveService extends Service {
    private static final String CHANNEL = "classroom_camera";

    static void start(Context context) {
        if (context.checkSelfPermission(android.Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            return; // a camera foreground service needs the camera permission first
        }
        try {
            Intent i = new Intent(context, KeepAliveService.class);
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i); else context.startService(i);
        } catch (RuntimeException ignored) {
            // not allowed right now (e.g. app in background); the kiosk starts it when opened
        }
    }

    static void stop(Context context) {
        context.stopService(new Intent(context, KeepAliveService.class));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Classroom camera",
                    NotificationManager.IMPORTANCE_LOW));
        }
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, KioskActivity.class),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, CHANNEL)
                : new Notification.Builder(this);
        Notification n = b.setContentTitle("Classroom attendance is running")
                .setContentText("Tap to open the classroom camera")
                .setSmallIcon(android.R.drawable.presence_video_online)
                .setContentIntent(open).setOngoing(true).build();
        try {
            ServiceCompat.startForeground(this, 1, n,
                    Build.VERSION.SDK_INT >= 30 ? ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA : 0);
        } catch (RuntimeException e) {
            // Not allowed now (e.g. started while the app was in the background); the kiosk works anyway
            // while it is on screen, and starts the service again when it is opened.
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
