# Build 6 interface validation

The production build before route splitting emitted one 2,763,010-byte JavaScript file (833,950 bytes gzip). With non-Schedule pages loaded at existing route boundaries, the initial JavaScript is 1,223,910 bytes (372,570 bytes gzip), a measured reduction of 55.7% raw and 55.3% gzip. Route chunks load only when opened. Schedule remains an eager import and its source and CSS were not changed.

Focused browser checks cover Today, Products/Inventory, Receiving, Transfers, Restock, Counts, Sale Entry/Review, Payment Evidence, Trades/Conditions, Closing, Reports and Schedule across the existing 390, 768 and 1440 pixel fixtures. Existing checks also exercise 200% zoom, keyboard input, scanner Enter and repeated/unknown scans, evidence file capture, role denial, account switching, empty/error states, and stale conflicts. The checks use local Vite and mocked API responses, so physical scanner/camera behavior and real shop network latency remain pilot checks.

Build output, measurement JSON and the manager Reports mobile screenshot are stored under `.local/build6`.
