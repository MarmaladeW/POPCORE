package ca.popcore.cloversync;

import android.accounts.Account;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.IBinder;
import com.clover.sdk.util.CloverAccount;
import com.clover.sdk.v3.order.Order;
import com.clover.sdk.v3.order.OrderConnector;
import com.clover.sdk.v3.order.OrderV31Connector;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONObject;

public final class SyncService extends Service {
    private static final String ENDPOINT = "https://popcore.store/clover-sandbox/device-snapshot";
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();
    private final ExecutorService reads = Executors.newSingleThreadExecutor();
    private final ExecutorService sender = Executors.newSingleThreadExecutor();
    private final Map<String, ScheduledFuture<?>> scheduled = new ConcurrentHashMap<>();
    private final PendingOrders pending = new PendingOrders();
    private final AtomicBoolean draining = new AtomicBoolean();
    private volatile boolean stopping;
    private final Set<String> knownIds = new LinkedHashSet<>();
    private OrderConnector connector;
    private SharedPreferences prefs;

    private final OrderV31Connector.OnOrderUpdateListener2 listener =
            new OrderV31Connector.OnOrderUpdateListener2() {
        @Override public void onOrderCreated(String id) { changed(id); }
        @Override public void onOrderUpdated(String id, boolean selfChange) { changed(id); }
        @Override public void onOrderDeleted(String id) { changed(id); }
        @Override public void onLineItemsAdded(String id, List<String> lines) { changed(id); }
        @Override public void onLineItemsDeleted(String id, List<String> lines) { changed(id); }
        @Override public void onLineItemsUpdated(String id, List<String> lines) { changed(id); }
        @Override public void onLineItemExchanged(String id, String oldLine, String newLine) { changed(id); }
        @Override public void onLineItemDiscountsAdded(String id, List<String> lines, List<String> discounts) { changed(id); }
        @Override public void onLineItemModificationsAdded(String id, List<String> lines, List<String> modifications) { changed(id); }
        public void onLineItemModificationsDeleted(String id, List<String> lines, List<String> modifications) { changed(id); }
        @Override public void onOrderDiscountAdded(String id, String discount) { changed(id); }
        @Override public void onOrderDiscountsDeleted(String id, List<String> discounts) { changed(id); }
        @Override public void onPaymentProcessed(String id, String payment) { changed(id); }
        @Override public void onRefundProcessed(String id, String refund) { changed(id); }
        @Override public void onCreditProcessed(String id, String credit) { changed(id); }
    };

    @Override public void onCreate() {
        super.onCreate();
        prefs = getSharedPreferences("bridge", MODE_PRIVATE);
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel("sync", "Clover order sync",
                NotificationManager.IMPORTANCE_LOW));
        Notification notice = new Notification.Builder(this, "sync")
                .setContentTitle("POPCORE sandbox sync")
                .setContentText("Listening for local Clover order changes")
                .setSmallIcon(android.R.drawable.stat_notify_sync).build();
        startForeground(1, notice);
        if (!prefs.getString("merchant_id", "").matches("[A-Za-z0-9]{13}") ||
                prefs.getString("secret", "").length() < 32) {
            status("Pair the sandbox merchant and bridge key before starting sync.");
            stopSelf();
            return;
        }
        Account account = CloverAccount.getAccount(this);
        if (account == null) {
            status("No Clover merchant account is available on this device.");
            stopSelf();
            return;
        }
        connector = new OrderConnector(this, account, null);
        connector.addOnOrderChangedListener(listener);
        if (!connector.connect()) {
            status("Could not connect to Clover's local order service.");
            stopSelf();
            return;
        }
        status("Listener registered; waiting for a local Clover order");
        synchronized (knownIds) { knownIds.addAll(prefs.getStringSet("known_ids", Collections.emptySet())); }
        scheduler.scheduleAtFixedRate(() -> {
            synchronized (knownIds) {
                for (String id : knownIds) schedule(id, 0);
            }
        }, 2, 2, TimeUnit.SECONDS);
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override public IBinder onBind(Intent intent) { return null; }

    private void changed(String id) {
        if (id == null || !id.matches("[A-Za-z0-9]{13}")) return;
        synchronized (knownIds) {
            knownIds.remove(id);
            knownIds.add(id);
            if (knownIds.size() > 5) knownIds.remove(knownIds.iterator().next());
            prefs.edit().putStringSet("known_ids", new HashSet<>(knownIds)).apply();
        }
        schedule(id, 100);
    }

    private void schedule(String id, long delayMs) {
        scheduled.compute(id, (key, prior) -> prior != null && !prior.isDone() ? prior :
                scheduler.schedule(() -> reads.execute(() -> read(id, 0)), delayMs, TimeUnit.MILLISECONDS));
    }

    private void read(String id, int attempt) {
        try {
            Order order = connector.getOrder(id);
            if (order != null && (order.getMerchant() == null ||
                    !prefs.getString("merchant_id", "").equals(order.getMerchant().getId()))) {
                status("Local Clover order belongs to a different or unknown merchant.");
                stopping = true;
                stopSelf();
                return;
            }
            JSONObject snapshot = SnapshotEncoder.encode(order);
            pending.offer(id, snapshot.toString());
            drain();
        } catch (Exception error) {
            if (attempt < 3) scheduler.schedule(() -> reads.execute(() -> read(id, attempt + 1)),
                    250, TimeUnit.MILLISECONDS);
            else status("Local Clover order is unavailable; waiting for the next update.");
        }
    }

    private void drain() {
        if (draining.compareAndSet(false, true)) sender.execute(() -> {
            try {
                Map.Entry<String, String> next;
                while (!Thread.currentThread().isInterrupted() && (next = pending.poll()) != null) {
                    String body = envelope(next.getValue());
                    int retrySeconds = 1;
                    while (!Thread.currentThread().isInterrupted()) {
                        String newer = pending.take(next.getKey());
                        if (newer != null) {
                            body = envelope(newer);
                            retrySeconds = 1;
                        }
                        int result = send(body);
                        if (result == 200) break;
                        if (result == 400 || result == 401 || result == 403 || result == 404 || result == 409 || result == 413) {
                            status("Pairing or snapshot rejected (HTTP " + result + "); check the bridge setup.");
                            stopping = true;
                            stopSelf();
                            return;
                        }
                        status("Connection delayed; retrying the latest order snapshot");
                        Thread.sleep(retrySeconds * 1000L);
                        retrySeconds = Math.min(30, retrySeconds * 2);
                    }
                }
            } catch (Exception error) {
                status("Device sync paused; reopen the app to reconnect.");
            } finally {
                draining.set(false);
                if (!stopping && !sender.isShutdown() && pending.hasPending()) drain();
            }
        });
    }

    private String envelope(String snapshot) throws Exception {
        JSONObject order = new JSONObject(snapshot);
        long sequence = prefs.getLong("sequence", 0) + 1;
        if (!prefs.edit().putLong("sequence", sequence).commit()) {
            throw new IllegalStateException("Could not save device sequence");
        }
        return new JSONObject().put("merchantId", prefs.getString("merchant_id", ""))
                .put("deviceId", prefs.getString("device_id", ""))
                .put("sequence", sequence).put("order", order).toString();
    }

    private int send(String body) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(ENDPOINT).openConnection();
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setConnectTimeout(1200);
            connection.setReadTimeout(1500);
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setRequestProperty("Authorization", "Bearer " + prefs.getString("secret", ""));
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            if (bytes.length > 128 * 1024) {
                status("Order snapshot is too large for the sandbox bridge.");
                return 413;
            }
            connection.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream output = connection.getOutputStream()) { output.write(bytes); }
            int code = connection.getResponseCode();
            if (code != 200) return code;
            prefs.edit().putLong("last_success", System.currentTimeMillis())
                    .putString("status", "Connected; sending local order changes").apply();
            return 200;
        } catch (Exception error) {
            return -1;
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void status(String message) { prefs.edit().putString("status", message).apply(); }

    @Override public void onTimeout(int startId, int fgsType) {
        status("Android ended this sandbox sync session; reopen the app later.");
        stopSelf();
    }

    @Override public void onDestroy() {
        stopping = true;
        if (connector != null) connector.disconnect();
        scheduler.shutdownNow();
        reads.shutdownNow();
        sender.shutdownNow();
        super.onDestroy();
    }
}
