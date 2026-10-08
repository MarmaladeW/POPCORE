package ca.popcore.cloversync;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.util.UUID;

public final class MainActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private EditText merchantInput;
    private EditText secretInput;
    private EditText endpointInput;
    private TextView status;
    private final Runnable refresh = new Runnable() {
        @Override public void run() {
            SharedPreferences prefs = getSharedPreferences("bridge", MODE_PRIVATE);
            long sentAt = prefs.getLong("last_success", 0);
            String message = prefs.getString("status", "Stopped");
            status.setText(message + (sentAt == 0 ? "" : "\nLast sent: " + new java.util.Date(sentAt)));
            handler.postDelayed(this, 1000);
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        SharedPreferences prefs = getSharedPreferences("bridge", MODE_PRIVATE);
        String deviceId = prefs.getString("device_id", "");
        if (deviceId.isEmpty()) {
            deviceId = UUID.randomUUID().toString();
            prefs.edit().putString("device_id", deviceId).apply();
        }
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int space = (int) (20 * getResources().getDisplayMetrics().density);
        layout.setPadding(space, space, space, space);
        TextView title = new TextView(this);
        title.setText("POPCORE Clover sandbox sync");
        title.setTextSize(22);
        layout.addView(title);
        TextView info = new TextView(this);
        info.setText("Read-only order updates to the POPCORE sandbox probe. Pair this device in the probe first.\nDevice ID: " + deviceId);
        info.setTextIsSelectable(true);
        layout.addView(info);
        endpointInput = new EditText(this);
        endpointInput.setHint("Probe URL (blank = popcore.store)");
        endpointInput.setSingleLine(true);
        endpointInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        endpointInput.setText(prefs.getString("endpoint", ""));
        layout.addView(endpointInput);
        merchantInput = new EditText(this);
        merchantInput.setHint("Sandbox merchant ID");
        merchantInput.setSingleLine(true);
        merchantInput.setText(prefs.getString("merchant_id", ""));
        layout.addView(merchantInput);
        secretInput = new EditText(this);
        secretInput.setHint(prefs.contains("secret") ? "Bridge key saved; leave blank to reuse" : "Bridge key");
        secretInput.setSingleLine(true);
        secretInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        layout.addView(secretInput);
        status = new TextView(this);
        layout.addView(status);
        Button start = new Button(this);
        start.setText("Start device sync");
        start.setOnClickListener(this::startSync);
        layout.addView(start);
        Button stop = new Button(this);
        stop.setText("Stop sync");
        stop.setOnClickListener(view -> {
            stopService(new Intent(this, SyncService.class));
            prefs.edit().putString("status", "Stopped").apply();
        });
        layout.addView(stop);
        setContentView(layout);
        // Sandbox convenience for emulator scripting: `am start ... --es endpoint <url> --es merchant <id>
        // --es secret <key> --ez autostart true` fills the pairing fields and starts sync.
        Intent intent = getIntent();
        if (intent.hasExtra("endpoint")) endpointInput.setText(intent.getStringExtra("endpoint"));
        if (intent.hasExtra("merchant")) merchantInput.setText(intent.getStringExtra("merchant"));
        if (intent.hasExtra("secret")) secretInput.setText(intent.getStringExtra("secret"));
        if (intent.getBooleanExtra("autostart", false)) startSync(start);
    }

    private void startSync(View view) {
        SharedPreferences prefs = getSharedPreferences("bridge", MODE_PRIVATE);
        String merchant = merchantInput.getText().toString().trim();
        String secret = secretInput.getText().toString().trim();
        if (secret.isEmpty()) secret = prefs.getString("secret", "");
        if (!merchant.matches("[A-Za-z0-9]{13}") || secret.length() < 32) {
            status.setText("Enter the 13-character merchant ID and bridge key.");
            return;
        }
        String endpoint = endpointInput.getText().toString().trim();
        if (endpoint.isEmpty()) endpoint = SyncService.DEFAULT_ENDPOINT;
        // Plain HTTP is allowed only to the emulator's own loopback (adb reverse / host alias).
        if (!(endpoint.startsWith("https://") || endpoint.startsWith("http://127.0.0.1:")
                || endpoint.startsWith("http://10.0.2.2:"))) {
            status.setText("Probe URL must use HTTPS, or plain HTTP only to 127.0.0.1 or 10.0.2.2 on this emulator.");
            return;
        }
        prefs.edit().putString("merchant_id", merchant).putString("secret", secret).putString("endpoint", endpoint)
                .putLong("session_started", System.currentTimeMillis())
                .putString("status", "Connecting to local Clover orders…").apply();
        secretInput.setText("");
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
        startForegroundService(new Intent(this, SyncService.class));
    }

    @Override protected void onResume() {
        super.onResume();
        handler.post(refresh);
    }

    @Override protected void onPause() {
        handler.removeCallbacks(refresh);
        super.onPause();
    }
}
