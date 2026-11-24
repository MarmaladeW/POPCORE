# 异常提醒 Watcher

Owner, 2026-11-24: "I wish to add a small claude api, to monitor any abnormal behavior within the app and notify the
admin and the manager" and "make sure that it does not leak any of our private data, in any way".
Tracked in MarmaladeW/popcore-work#81. Code: `popcore_app/watcher.py`, `blueprints/watch.py`, page `/watch`,
设置 → 异常提醒 / Alerts.

## 给老板的说明（中文）

**它查什么**（POPCORE 自己每小时查一次，不用 Claude）：

- **损耗**：记成「被偷」「丢失」「其他」的损耗；一次损坏 3 个以上。
- **盘点差**：同一天同一店同一产品，盘点比账上少 10 个以上，或多 30 个以上（同一天楼上楼下互相搬的不算）。
- **日报现金差**：日报的 现金 实收 和 应收 差 $20 以上。
- **日报库存差异多**：一份日报留下 5 条以上没处理的库存差异（楼下不够、还没盘点、库存没移完）。
- **同步卡住**：网站库存同步或图鉴同步连续出错一小时，或一小时没运行。

日报以后不贴了，日报那两项就自己不再出现，不用改。

**告诉谁**：管理员看全部；每家店的经理只看自己店的；同步问题只给管理员。提醒出现在首页「异常提醒」和
「异常提醒」页面，看过点「已查看」。**要紧的**（被偷/丢失、盘点少 25 个以上、现金差 $50 以上、同步卡住）
马上推送到手机；其他的每天早上 Claude 看完后再推送。手机推送只写「N 条新提醒」，不写内容。
手机要先在「网站订单」页打开「本设备通知」。

**Claude 做什么**：每天早上 7:00（可改）用 Claude Opus 5.5 看一遍新提醒，给每条写一句中英文说明；
看起来正常的标成「Claude 认为正常」折起来（不会删掉，要紧的不能被它折起来）。

**隐私**：Claude 只看到代号和数字，例如「S1 店的 P3 被偷 1 盒，30 天内其他损耗 0 次」。
它看不到店名、产品名、员工、备注、日期或任何文字；POPCORE 自己把名字换回去。
每次发了什么都存着，管理员在「异常提醒」页能看到全部内容。邮件不用，免得内容进邮箱服务商。

**花费**：大约每月 US$1–2。设置里有每日上限（默认 US$0.50），到了就不再调用，提醒照样出，只是没有 Claude 的说明。

**开关**：设置 → 异常提醒 / Alerts。默认关闭，上线后由管理员打开。也可以只关 Claude、留着规则。

## Owner note (English)

**What it checks** (POPCORE by itself, every hour, no Claude):

- **Losses**: a write-off marked stolen, missing or other; 3 or more broken at once.
- **Count gaps**: a count of one product at one store on one day 10+ below the book, or 30+ above (pieces moved
  between places counted the same day don't count).
- **日报 cash gaps**: a daily report's cash taken and cash expected more than $20 apart.
- **Many report notices**: one daily report leaving 5+ open stock notices (not enough on the floor, never counted,
  stock line not moved in full).
- **Stuck sync**: the website stock sync or the Dex sync failing for an hour, or not run for an hour.

When 日报 pasting stops, the two 日报 checks simply go quiet.

**Who is told**: admins see everything; each store's managers see their own store's; sync problems go to admins
only. Alerts show on the manager home and on the 异常提醒 Alerts page; "已查看 Checked" clears one. **Urgent** ones
(stolen/missing, a count 25+ short, a cash gap of $50+, a stuck sync) are pushed to phones at once; the rest after
Claude's morning review. A push says only "N new alerts", never what. Phones turn pushes on from the Web orders page.

**What Claude does**: every morning at 07:00 (changeable), Claude Opus 5.5 reads the new alerts and writes one line
in Chinese and English for each; ones that look normal are folded under "Looked normal to Claude" (never removed;
it cannot fold an urgent one).

**Privacy**: Claude sees only stand-in codes and numbers, e.g. "S1, P3, stolen, 1 box, 0 other losses in 30 days".
Never a store, product or person name, a note, a date or any free text; POPCORE puts the names back itself. What was
sent is kept for every run and admins can read all of it on the Alerts page. No email, so nothing sits in a mail
provider.

**Cost**: about US$1–2 a month. 设置 has a daily cap (US$0.50 by default); past it Claude isn't called and the alerts
still come, just without Claude's line.

**Switch**: 设置 → 异常提醒 / Alerts. Off by default; an admin turns it on after deploy. Claude can be turned off
alone, keeping the rules.

## How it works

- `run_due()` runs every 10 minutes from app.py's scheduler (not when `DISABLE_SCHEDULER` is set): the rules once an
  hour, the review once a Toronto day after `watcher_review_time`, or within 10 minutes of an admin's 立即检查 Check now.
  Nothing runs while `watcher_enabled` is off.
- Rules look at the last 3 business days. An alert the rules stop finding inside that window (a loss undone, a report
  fixed, notices checked, the sync working again) settles itself (`resolved`); a checked one stays checked.
- A failing sync waits as `pending` for an hour before it is an alert, so one bad pass doesn't page anyone.
- Review: one Messages API call (`claude-opus-5-5`, effort medium, JSON-schema reply, server-side fallbacks
  `fallbacks: "default"`). The request carries only the system prompt (`watcher.SYSTEM`, no names) and the stand-in
  payload built from `SAFE_FACTS`; `test_watcher.py` checks the whole HTTP body with the real SDK. Before the call,
  today's spend plus the most the call could cost (every character a token, the whole output, the dearest price)
  must fit under the cap. A failed call is counted at that most, and keeps only the error's class name.
- Pushes reuse the web-order push devices (`push_notifications.py`, encrypted to each device). Who is admin or manager
  comes from Auth0 at send time (no copy of roles in the database); managers need `inventory_access` at the store;
  removed accounts get nothing. With Auth0 out of reach the push waits for the next run.

## Deploy

1. `popcore_app/requirements.txt` gains `anthropic==2.13.0`; `popcore-deploy` warns and prints the `pip install`
   line to run first.
2. `ANTHROPIC_API_KEY` in `/root/POPCORE/popcore_app/.env` (the 日报 parser already reads the same key, if set). Never
   print it; without it the watcher runs the rules only.
3. Migration `watch_alerts` (two new empty tables).
4. 设置 → 异常提醒 / Alerts → turn on. 立即检查 Check now on the Alerts page runs the rules at once.
