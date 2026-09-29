package ca.popcore.cloversync;

import java.util.concurrent.TimeUnit;

public final class SyncSessionTest {
    public static void main(String[] args) {
        long start = 1_000_000L;
        long sixHours = TimeUnit.HOURS.toMillis(6);
        if (SyncSession.remainingMillis(start, start) != sixHours ||
                SyncSession.remainingMillis(start, start + sixHours / 2) != sixHours / 2 ||
                SyncSession.remainingMillis(start, start + sixHours - 1) != 1 ||
                SyncSession.remainingMillis(start, start + sixHours) != 0 ||
                SyncSession.remainingMillis(start, start + sixHours + 1) != 0 ||
                SyncSession.remainingMillis(start, start - 1) != 0 ||
                SyncSession.remainingMillis(0, start) != 0) {
            throw new AssertionError("Six-hour session cutoff failed");
        }
    }
}
