# Clover sandbox connection test

This is an isolated, single-test-merchant probe, authorized on 2026-09-13. It runs
separately from the POPCORE Flask application. It never imports `popcore_app/app.py`,
opens the inventory database, posts stock, or changes Clover orders/payments.
All Clover requests are pinned to the sandbox hosts. POST requests are limited to
OAuth token exchange/refresh. There is no production-mode switch.

## Local setup prepared in this task

- Service: `scripts/clover_sandbox.py`
- Checks: `popcore_app/tests/test_clover_sandbox_probe.py`
- Private configuration: `.local/clover-sandbox/config.json` (ignored, mode 0600)
- Separate database: `.local/clover-sandbox/data/sandbox.sqlite3`
- Local address: `http://127.0.0.1:5055/clover-sandbox/`
- Local administrator username: `popcore`; password is `ADMIN_PASSWORD` in the private configuration.

Existing Flask and requests packages are reused locally. The Droplet has a separate
virtual environment; the main application's environment and frontend were not changed.

Run from the repository root on this Mac:

```sh
.local/frontend-venv/bin/python -m unittest discover -s popcore_app/tests -p test_clover_sandbox_probe.py -v
.local/frontend-venv/bin/python scripts/clover_sandbox.py --config .local/clover-sandbox/config.json
```

The CLI binds only to loopback and disables access logs to avoid logging OAuth codes
and the webhook path. Restart the process after editing configuration. To use a
different port, add `--port 5056` and update `PUBLIC_URL` accordingly.

## Deployment verified 2026-09-14 UTC

The isolated service is live at `https://popcore.store/clover-sandbox/` on the
existing DigitalOcean Droplet. Installation was explicitly authorized by the user.

- Code/runtime: `/opt/popcore-clover-sandbox`; separate virtual environment.
- Service/account: `popcore-clover-sandbox`; one Gunicorn worker, four threads,
  bound only to `127.0.0.1:5055`. The account has no login shell.
- Private configuration: `/opt/popcore-clover-sandbox/config.json` (0600).
- Separate state: `/var/lib/popcore-clover-sandbox` (0700), SQLite file (0600).
- nginx: one added `/clover-sandbox/` location in the existing HTTPS virtual host.
  Original config saved at `/opt/popcore-clover-sandbox/nginx-before-clover-sandbox.conf`.
- Checks: all 10 tests passed both locally and on the Droplet; dependency check
  passed; public health returned sandbox JSON; unauthenticated admin returned 401;
  authenticated admin returned 200; OAuth launch used the expected sandbox host
  and HTTPS callback; an invalid callback returned 400.
- Temporary deployment SSH access was removed; a subsequent login with that key
  was rejected. The local temporary key files were deleted.
- Main `/` and `/schedule` returned 200. The main service PID and start timestamp
  were unchanged. Installed script SHA-256 matched the local source.

Clover authorization and order/payment notification delivery have **not** been
verified. The webhook verification challenge passed on 2026-09-14; see the latest
connection attempt below. The test merchant is not connected yet.

### Authorization troubleshooting, 2026-09-14

The Clover authorization page remained on its loading image. Browser errors showed
`Cannot read properties of undefined (reading 'get')` in Clover's OAuth
`v2-merchants.afterModel`, after an app lookup failed. The developer dashboard later
showed that the app had a Canada-only free tier but the only test merchant was US.
Its REST URLs were also empty. Saved the Site URL below and the relative Alternate
Launch Path `/clover-sandbox/connect`, keeping OAuth Code and CORS blank.

Separately, the private merchant setting contained the app ID. It was corrected
locally and on the Droplet to the merchant ID displayed by the signed-in sandbox
dashboard. Only the sandbox service was restarted, and its HTTPS health check
passed afterward. Subsequently created **POPCORE Canada Sandbox** in Canada/CAD
using sample test address/phone information. Both private local configurations and
the server now allow only this Canadian test merchant, not the original US one.

OAuth launch now includes the configured `merchant_id`, avoiding selection of a
different signed-in test store. The updated check passed locally and on the server
(10 tests); deployed source hashes matched. A browser retry passed the earlier app
lookup crash and reached the Canadian merchant's Clover App Market app detail page.
App installation and token exchange still require verification.

The installation page and the standalone Canadian Merchant Dashboard both remain
on **Loading**. Chrome DevTools shows 18 HTTP 404 errors for the current Clover
web-portal release's `assets/locales/en-CA/*.json` files, including `boot-loader.json`.
The dashboard supports `lng=en-US`; this removed those resource errors without
changing the merchant's country or currency, but did not resolve the loading screen.
Requests for the Canadian merchant, current employee, installed apps and permissions
returned HTTP 200. The earlier OAuth JavaScript exception did not recur with the
Canadian merchant selected. No app connection/token exchange has completed.

For Clover developer support, reproduce with the Canadian sandbox merchant's
dashboard and App Market preview; report the missing en-CA locale assets and the
persistent loading screen even with `lng=en-US`. Do not send app secrets, session
cookies, OAuth codes/state, or unsanitized network exports.

### Configuration and website audit, 2026-09-14

Rechecked the deployed script and test hashes against local files: exact matches.
The server has the intended public URL, sandbox app ID and Canadian merchant ID;
required secrets are present and the config/database permissions remain 0600.
Secret presence does not establish that Clover accepts the app secret.

Fresh HTTPS checks confirmed that `/connect` returns 302 to the sandbox OAuth
endpoint with the intended merchant, app and callback. Its session cookie is
Secure, HttpOnly, SameSite=Lax and scoped to `/clover-sandbox`. An authenticated
HTTP client returning with the issued state but without an authorization code
received the expected 400, without a redirect loop or a token exchange. This
checks routing/state handling, not a real cross-site browser authorization.

The callback also requires Basic authentication. Cached browser credentials may
cover the return navigation, but that behavior remains unverified in a completed
Clover round trip. The app forbids iframe embedding; the observed launch was a
top-level navigation. Neither protection was removed to bypass the loading screen.
Blank CORS is appropriate for this server-side API client. The current Clover
settings documentation describes Alternate Launch Path as using the Site URL's
base domain, matching the saved relative path.

The standalone Clover dashboard stalling supports investigating Clover's merchant
session/provisioning or frontend, but does not prove a provider defect or rule out
all account/app settings. App installation, secret validity, real callback/token
exchange and webhook delivery remain unverified. The developer session had expired
during this audit; a fresh review of saved fields requires signing in again.

After the user signed in again, re-read the saved settings: REST Clients, Read
Employees/Inventory/Orders/Payments, the exact Site URL and Alternate Launch Path
above, CODE response, all merchants eligible. Installs explicitly reports zero.
The Test Merchants table confirms the configured merchant is CA and the original
merchant is US. Launch Dashboard from that table stalls for both merchants,
independently of POPCORE's connect route.

With Chrome's cache disabled, the US dashboard still stalled. Its Network panel
showed a fetch to `/dashboard/m/selfboarding/v2/accounts/<account-id>/status`
returning HTTP 200 with `Content-Type: text/html`; Preview identified the response
as Clover Dashboard. This suggests a relative URL/routing or bootstrap problem,
but its causal role in the spinner has not been proven. A `domain:popcore.store`
filter showed zero of 83 captured requests. Thus this direct-dashboard failure
reproduces without a browser request to POPCORE. Restored the cache setting and
returned the user's tab to Test Merchants. No app/server settings changed.

Safari comparison after a separate user login: the developer dashboard loaded,
but the original US merchant launched from Test Merchants stayed on its spinner
after an additional 20-second wait. Navigating to the known Canadian dashboard URL
also stayed on its spinner after an additional 20 seconds. This reproduces the
merchant-dashboard failure across Chrome and Safari, making a Chrome-specific
session/extension cause less likely. It does not distinguish Clover account
provisioning from a sandbox software defect or a shared network issue. No Safari
privacy/security settings were changed. The Canadian merchant Settings dialog
states that settings feature is available only for US test merchants; this is
separate from the Launch Dashboard action and is not proof Canada is unsupported.

### Real connection attempt after renewed sign-in, 2026-09-14

The user completed the First Data developer sign-in. Launching **POPCORE Canada
Sandbox** from Test Merchants established the merchant session: the next OAuth
attempt advanced past the merchant login form. The Canadian dashboard and the
app installation page still remained on **Loading**. The app's Installs page
reported **Total Merchants: 0**. No OAuth callback/token exchange completed.

Webhook setup could proceed independently through the developer app settings:

- Clover sent a real verification challenge to the hosted probe. The probe
  displayed the received code, and Clover accepted it through **VERIFY**.
- Saved **Orders** and **Payments** event subscriptions. The app settings page
  displayed the saved webhook URL and both subscriptions.
- Copied Clover's resulting webhook authentication code into the existing server
  configuration's `CLOVER_WEBHOOK_AUTH` through a hidden input. The atomic write
  retained the configuration owner/group and mode 0600. Only the isolated
  `popcore-clover-sandbox` service was restarted; it returned **active**.
- Fresh HTTPS checks returned health **200**, administrator status **200** with
  webhook authentication configured and merchant disconnected, and orders **502**
  with `Connect the Canadian test merchant first.`

This proves the real Clover webhook verification round trip. It does **not** prove
authenticated order/payment notification delivery, API order reads, or a completed
sale. The notification list remained empty. Local private configurations were
not updated with the new webhook authentication code; the hosted receiver is the
configured endpoint. No Clover order/payment or inventory writes were performed.

Next prerequisite: resolve the test merchant dashboard/app installation loading
failure, then complete OAuth and create a disposable sandbox order to verify its
REST data and authenticated notification. The already verified webhook URL does
not need another verification challenge.

### Developer-to-merchant access evidence, 2026-09-14

The official installation path, **Market Listing > Preview in App Market**, exposed
a more specific failure after its initial render. The preview button became
disabled with: "You'll be able to preview this app once the owner adds you as an
employee". Separately, **Developer Settings > Members** showed the signed-in
developer account as **OWNER**, **Active**.

Clover documents developer and merchant accounts as separate access contexts.
Its employee-assignment procedure requires opening the test Merchant Dashboard,
which remains inaccessible here. These observations support asking Clover to
check developer-to-merchant identity linkage/provisioning; they do not prove the
underlying cause of the loading screen. No roles, memberships, or credentials
were changed during this investigation.

Official references:
- https://docs.clover.com/dev/docs/gdp-install-your-app-to-a-test-merchant
- https://docs.clover.com/dev/docs/manage-global-developer-account
- https://docs.clover.com/dev/docs/manage-global-developer-roles
- https://docs.clover.com/dev/docs/developer-technical-support

A reviewable support request is saved privately at
`.local/clover-sandbox/support-request.md`. It includes the developer account and
sandbox identifiers, reproduction steps, and evidence limits. After the user's
approval and a prose edit, it was **sent** from the developer owner's Gmail account
to `developer-relations@devrel.clover.com`. Gmail returned message/thread ID
`1a0a064b9bb4c174` with the `SENT` label. Subject: "Global developer experience -
sandbox app preview blocked despite Owner access". No support response has been
checked yet.

### Live connection evidence, 2026-09-22

The earlier Clover sandbox block is resolved. **POPCORE Order Sync** was installed
on **POPCORE Canada Sandbox**, the hosted OAuth callback exchanged a real code, and
the hosted probe can read that merchant's orders using its server-side app token.

One disposable API-created sandbox order, `FA69FJE53VYC8`, established the current
read and webhook path:

- Currency was CAD; line item `5WJVFTK3DWZB8` had price `100` minor units and raw
  `unitQty` `1`.
- The order was still open: `total` was null, `paymentState` was `OPEN`, and there
  were no payments or employee ID. This is not evidence of a completed $1 sale.
- The authenticated webhook received both `CREATE` and `UPDATE` for the same order.
- A temporary merchant API token used only to create this disposable order was
  deleted in Clover, and its local configuration entry was removed. The installed
  app's OAuth token remains separate and continues to support read-only probing.

The probe now preserves selected order lifecycle, source item, discount, tender,
payment and employee identifiers while excluding raw customer, card and token data.
Automated Clover sandbox/rehearsal checks pass locally. A later Register-created cash
sale delivered authenticated order and payment notifications. After deploying the
read-only review page on 2026-09-22, the sandbox API independently returned that same
order as `PAID`, total `100` minor units, with a successful `$1.00` Cash payment.
The full manual-discount pass then succeeded on order `REBW64Z2DJDJR`: a `$30.00`
Custom Item appeared while open, the review page suggested a `$1.00` discount and
`$29.00` payment, Clover reported that discount and new total, and the completed
order returned as `PAID` with a successful `$29.00` Cash payment. Authenticated order
updates and a payment-create notification also reached the receiver.

## User inputs and next steps

1. Confirm the test merchant uses Canada/CAD, then set its 13-character ID in
   `CLOVER_MERCHANT_ID`. Only that merchant is accepted during OAuth and webhooks.
2. Rotate the sandbox App Secret exposed in a prior screenshot using Clover's
   supported procedure. Enter the replacement directly in `CLOVER_APP_SECRET` in
   the local private file; never send it in chat. If rotation requires recreating
   the unused app, also update `CLOVER_APP_ID` and its read permissions.
3. Open the live sandbox page. Username is `popcore`; use `ADMIN_PASSWORD` from the
   local private configuration. Click **Connect Canadian test merchant** after
   saving the Clover REST fields below.

## Public hosting settings

Installed paths on the existing domain:

| Clover field | Value |
| --- | --- |
| Site URL | `https://popcore.store/clover-sandbox/` |
| Alternate Launch Path | `/clover-sandbox/connect` |
| CORS Domain | Blank |
| Default OAuth Response | Code |
| Webhook URL | Copy the private URL shown on the sandbox service's administrator page |

The callback is `https://popcore.store/clover-sandbox/callback` and lies beneath the
Site URL. The current Clover editor explicitly describes Alternate Launch Path as
a path extension using the Site URL's domain. The full launch URL is
`https://popcore.store/clover-sandbox/connect`.

Deployment must use a separate OS process, separate private config/data directory,
and no production POPCORE environment file. Set PUBLIC_URL to
`https://popcore.store/clover-sandbox`, and DATA_DIR to that separate server directory.
Use one Gunicorn worker: token rotation is protected only within one process.

Example launch command after installing the script and its configuration into a
reviewed server directory (paths below must be adapted to the inspected host):

```sh
# Set only the path, not credential contents, in the process environment.
export CLOVER_SANDBOX_CONFIG=/opt/popcore-clover-sandbox/config.json
gunicorn --chdir /opt/popcore-clover-sandbox --bind 127.0.0.1:5055 --workers 1 --threads 4 \
  --timeout 60 'clover_sandbox:from_config()'
```

Do not enable access logs containing URLs or query strings.

Add only this location to the existing HTTPS nginx virtual host, after inspecting
its active configuration and backing it up. Do not replace the virtual host with
the repository's generic nginx template.

```nginx
location ^~ /clover-sandbox/ {
    access_log off;
    error_log /dev/null crit;
    client_max_body_size 128k;
    proxy_pass http://127.0.0.1:5055;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 60s;
}
```

The service sends its own no-store, no-referrer, frame-denial and CSP headers. Keep
inherited TLS security headers. Run `nginx -t` before reloading. Verify the existing
POPCORE page still works and `/clover-sandbox/health` returns sandbox health JSON.
Then verify the sandbox page requires its administrator password. Rollback removes
only the added location and stops only the sandbox process.

## Clover test sequence

1. Save the REST settings only after the public endpoints work. The sandbox app
   should be REST Clients / Web, not a semi-integrated POS, with read permissions
   for Orders, Payments, Inventory, Employees (and Merchant if available).
2. Connect/install the app on the Canadian test merchant, then visit the service's
   `/connect` route in the same browser. Sign into the sandbox merchant. The callback
   validates the browser-bound, one-use OAuth state and exact merchant ID, exchanges
   the code on the server, and saves tokens only in its private SQLite file.
3. In Clover Webhooks, use the private URL from the service page and send the
   verification code. Reload the service page to read that code and paste it into
   Clover. Before webhook auth is configured, only the verification challenge is
   accepted; normal notifications are blocked.
4. After verification, copy Clover's webhook Auth Code into `CLOVER_WEBHOOK_AUTH`
   in the private configuration and restart the service. Subscribe to Orders and
   Payments. Do not share the code or private webhook URL in screenshots.
5. Create/change one fake order through the test Merchant Dashboard or a supported
   test device/emulator. Reload the service's notification page, then select
   **Review sandbox checkout**. This queries up to 20 orders from the sandbox API and
   displays selected order facts, line items, a read-only discount estimate and payment
   results. The raw JSON remains available at **Read test orders (JSON)**. Amounts are
   minor currency units; `unitQty` remains the raw Clover value, not an inferred stock unit.
6. Change quantities/discounts, complete a test payment, and check the same order ID.
   Record timing and failures. Repeated webhook events must not create duplicates.

## Evidence and deliberate limits

Local automated checks use mocked provider responses; they do not prove Clover
authorization, provider delivery, field availability or checkout behavior by themselves.
The live evidence above separately verifies merchant OAuth, one current order read,
authenticated order/payment webhook delivery, and one completed Register cash checkout
read through the sandbox API. The manual whole-dollar discount path is verified for a
non-taxed custom item; the tax-adjustment path remains unverified.

This probe stores webhook metadata and fetches current order facts when requested.
The review page calculates the existing whole-dollar payment suggestion and a pre-tax
discount estimate for simple undiscounted sandbox orders. It is read-only: staff must
enter the discount in Clover and confirm Clover's total before payment. It does not yet
automatically refresh phone screens, reconcile all historical orders, post inventory,
upload evidence, or integrate RewardUp. Those remain separate work after connection
feasibility is established. Expired authorization
without a usable refresh token requires reconnecting. Refresh recovery after a process
crash is intentionally not implemented in this single-merchant test tool.

The fake store does not connect to the actual Station Duo. API-originated sandbox
events do not establish when an in-progress device order reaches Clover's cloud.

Official references:
- https://docs.clover.com/dev/docs/merchant-dashboard-left-navigation-oauth-flow
- https://docs.clover.com/dev/docs/high-trust-app-auth-flow
- https://docs.clover.com/dev/docs/refresh-access-tokens
- https://docs.clover.com/dev/docs/webhooks
- https://docs.clover.com/dev/docs/gdp-manage-test-merchants-accounts
