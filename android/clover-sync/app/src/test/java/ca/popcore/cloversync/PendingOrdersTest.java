package ca.popcore.cloversync;

import java.util.Map;

public final class PendingOrdersTest {
    public static void main(String[] args) {
        PendingOrders pending = new PendingOrders();
        pending.offer("order-a", "one item");
        pending.offer("order-b", "other order");
        pending.offer("order-a", "three distinct items");
        if (!Map.entry("order-a", "three distinct items").equals(pending.poll())) throw new AssertionError();
        if (!Map.entry("order-b", "other order").equals(pending.poll())) throw new AssertionError();
        if (pending.poll() != null) throw new AssertionError();
        pending.offer("order-a", "four items");
        pending.offer("order-a", "five items");
        if (!"five items".equals(pending.take("order-a"))) throw new AssertionError();
        if (pending.hasPending()) throw new AssertionError();
    }
}
