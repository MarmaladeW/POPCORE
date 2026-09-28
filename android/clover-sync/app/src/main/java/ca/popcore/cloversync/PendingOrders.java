package ca.popcore.cloversync;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.AbstractMap;

final class PendingOrders {
    private final LinkedHashMap<String, String> latest = new LinkedHashMap<>();

    synchronized void offer(String orderId, String snapshot) {
        latest.put(orderId, snapshot);
    }

    synchronized Map.Entry<String, String> poll() {
        if (latest.isEmpty()) return null;
        Map.Entry<String, String> first = latest.entrySet().iterator().next();
        Map.Entry<String, String> result = new AbstractMap.SimpleImmutableEntry<>(first);
        latest.remove(first.getKey());
        return result;
    }

    synchronized boolean hasPending() { return !latest.isEmpty(); }

    synchronized String take(String orderId) { return latest.remove(orderId); }
}
