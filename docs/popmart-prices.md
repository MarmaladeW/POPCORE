# POP MART 价格 POP MART prices

Owner, 2026-11-25 (MarmaladeW/popcore-work#91): read POP MART Canada's names, prices and stock as "a weekly regular
thing" in popcore.store. The owner chose:

- a **compare screen**: same / we are higher / we are lower / not found. It changes no price and sends nothing to the
  website.
- a weekly read on **Monday at 6:00 AM** Toronto time.
- the **March scraper removed** (`scraper.py`, with Mr. Pen and Whoopea and the old title matcher).

POP MART US is read too, in US dollars, as a reference. Prices are compared only with POP MART Canada.

## Where

Products → **POP MART 价格 POP MART prices** (`/products/popmart-prices`). Managers only.

The screen has:

- **Figures**: we are higher, we are lower, same price, not at POP MART CA.
- **Filters**: by those states, by kind (box, whole set, item), and a search.
- **One row per POPCORE product**:
  - our price;
  - POP MART CA's price, option, and in stock or sold out, with a link to the product on popmart.com;
  - the difference;
  - POP MART US's price and stock.
- **Read now**: the read starts within a minute and takes about two.
- **Link**, for a product whose name differs from POP MART's. See Matching.

## Reading

`popmart_prices.py`:

- POP MART's search pages, `https://www.popmart.com/{ca,us}/search/<word>?page=N&pageSize=100`, carry their products in
  the page (`<script id="__NEXT_DATA__">` → `props.pageProps.serverSearchRaw`: `total`, `list[]` with `id`, `title` and
  `skus[]`). A plain request reads them; no browser is needed.
- Seven words reach the catalogue: Series, Figure, Plush, Pendant, Box, MEGA, POP BEAN. Each is read to its last page,
  with a pause of one second between pages.
- 25 Nov 2026: 1,030 CA products (1,726 options) and 1,314 US products (2,184 options). Both regions took 90 s. The
  server reached both.
- **Price** is `skus[].price` in cents. `discountPrice` lower than it is POP MART's sale price, and that is the price
  compared.
- **Stock** is `skus[].stock.onlineStock`. It is a flag (0, 1 or 2), never a count, so the screen says only in stock or
  sold out.
- Each region keeps only its newest good list (`popmart_items`, one row per POP MART option). Every read is logged in
  `popmart_reads`.

**A bad read keeps the earlier list**, and the screen shows why. A read is bad when:

- a page fails or has no product data (for example, the site changed or blocked the read);
- it finds under 60% of the products of the last good read.

## When

`app.py` runs `popmart_prices.run_due()` every minute, never two at once, while the scheduler is on. It reads:

1. at once, when a manager pressed Read now (`app_settings.popmart_read_requested`);
2. otherwise, once Monday 6:00 AM Toronto time has passed (EST or EDT) and a region has no good read since then. A failed
   region tries again an hour later, until a read works that week.

A deploy with no read yet that week reads within its first minute.

A read still "running" after 20 minutes was cut off by a restart: it is closed as failed, and the next minute's check
may start again.

## Matching

POP MART's product numbers differ per region, so products are joined **by name**, never by number. `norm()` compares
names:

- in lower case;
- with × read as x and & as and;
- without punctuation;
- without a plural s;
- without the words POP MART writes on some names and not others: Blind Box, The, POP MART.

POPCORE's side:

- verified POP MART products (`PM…`) that are a box (`random_box`), an item (`ordinary`) or a whole set (`sealed_set`).
- Confirmed designs are left out: POP MART does not sell them.
- A box compares with POP MART's **Single box** option.
- An item compares with its one option, or the cheapest.
- A whole set compares with its box's POP MART product's **Whole Set** option.

On 25 Nov 2026, 446 of the 753 rows matched by name. Most of the 307 that did not are products POP MART CA does not
sell: older MEGA, regional exclusives, sold-through series. Others are named differently, often by a typo on our
side. For those:

- The screen offers up to three close POP MART names ("Is it: …?").
- A manager can link a product to any POP MART CA name (`popmart_links`, one per product). A whole set follows its box's
  link. The old fuzzy `match_title` paired unrelated products at its 85 score and is gone.

## Not done (owner's decisions in #91)

What to do with "we are lower" products (the website's Sales) and with "we are higher" ones (match, or a list to
approve). Nothing here does either.

## Checks

`python -m unittest discover -s popcore_app/tests -p test_popmart_prices.py` covers:

- reading every page, with a fake POP MART;
- same, higher, lower and not found, and a set following its box;
- × and plural names;
- a POP MART sale price;
- links;
- managers only;
- a failed or thin read keeping the list;
- the Monday 6:00 timing in winter and summer time, and the hourly retry;
- Read now;
- a read cut off by a restart.
