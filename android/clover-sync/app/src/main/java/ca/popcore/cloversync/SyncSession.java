package ca.popcore.cloversync;

import java.util.concurrent.TimeUnit;

final class SyncSession {
    private static final long DURATION_MILLIS = TimeUnit.HOURS.toMillis(6);

    static long remainingMillis(long startedAt, long now) {
        if (startedAt <= 0 || now < startedAt) return 0;
        return Math.max(0, DURATION_MILLIS - (now - startedAt));
    }
}
