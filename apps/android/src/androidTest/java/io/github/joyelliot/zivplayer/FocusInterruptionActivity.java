// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer;

import android.app.Activity;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.os.Bundle;
import android.util.Log;
import android.widget.TextView;

/** A real, separate-UID foreground focus client, confined to the test APK. */
public final class FocusInterruptionActivity extends Activity {
    private AudioManager manager;
    private AudioFocusRequest request;
    private TextView status;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        manager = getSystemService(AudioManager.class);
        status = new TextView(this);
        status.setText("ZivPlayer audio focus test");
        status.setTextSize(24);
        status.setKeepScreenOn(true);
        status.setPadding(32, 64, 32, 32);
        setContentView(status);
    }

    @Override protected void onResume() {
        super.onResume();
        request = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                .setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                .setOnAudioFocusChangeListener(change -> { })
                .build();
        int result = manager.requestAudioFocus(request);
        status.setText("ZivPlayer audio focus test\nRequest result: " + result);
        Log.i("ZivFocusProbe", "Transient focus request result=" + result);
    }

    @Override protected void onPause() {
        if (request != null) {
            manager.abandonAudioFocusRequest(request);
            request = null;
        }
        super.onPause();
        finish();
    }
}
