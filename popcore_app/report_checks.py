"""Format checks on a pasted daily report (owner, 2026-11-06): anything written in a way that may move
stock wrongly is pointed out by its line in the paste, quoting that line, with what POPCORE will do with
it. `fix` is most likely a mistake; `check` may be meant. A check never stops the save by itself: the rows
it points at are still accepted, corrected or removed on the paste screen as before."""
import re
from collections import defaultdict

from report_stock import _set_for_box, _stock_product

SECTION_LABELS = {
    'pos': '卡机', 'cash': '随手记', 'refill': '出库入店', 'stock_in': '入店', 'stock_out': '出店',
    'display_sold': '卖display', 'break_display': '拆display', 'display_stock_in': '入display',
    'claw_prize': '娃娃机', 'store_transfer': '出库', 'claw_stock_in': '入娃娃机',
}
SALES = ('pos', 'cash')
STOCK_LINES = ('refill', 'stock_in', 'stock_out', 'display_sold', 'break_display', 'display_stock_in', 'claw_prize')
# One sales line this big is worth a second look (owner, 2026-11-06: "萌粒快乐开涮*20").
BIG_SALE = 10
_TIMES = re.compile(r'(?:^|[^A-Za-z])[x×X]\s*\d+\s*$')
_NO_SAVE = '改好数量或移除这一行才能保存；移除就不移库存。Fix the quantity or remove the row to save.'


def _check(level, lines, problem, section=None):
    return {'level': level, 'lines': sorted(set(lines)), 'section': SECTION_LABELS.get(section, section),
            'problem': problem}


def _key(name):
    return re.sub(r'\s+', '', (name or '').casefold())


def _source(item):
    """The text an item was read from: the original kept in its note, else its name."""
    kept = re.search(r'原文: (.*?)(?:；|$)', item.get('note') or '')
    return kept.group(1) if kept else item['raw_name']


def text_checks(boundaries, items, unknown_items, handled=()):
    """Checks that need only the paste: headings, quantities and repeated lines.
    `boundaries` are (line number, section, heading text); `items` are the parsed rows with their line;
    lines in `handled` already have a check of their own."""
    out = []
    items = [item for item in items if item.get('line') and item['line'] not in handled]
    headings = [(line, section, text) for line, section, text in boundaries if section not in ('skip',)]
    first = min((line for line, _, _ in boundaries), default=None)
    early = [item['line'] for item in items if first is not None and item['line'] < first]
    if early and boundaries:
        out.append(_check('check', early, '在第一个标题之前：按 随手记 Non-POS 销售读。'
                                          'Before any heading: read as 随手记 sales.', 'cash'))
    for line, section, text in headings:
        if section != 'unknown':
            continue
        under = [item['line'] for item in unknown_items if item.get('unknown_header') == text]
        problem = (f'不认识的标题「{text}」：下面 {len(under)} 行放在 Failed，选好分区或 Skip 才会保存。'
                   'Unknown heading: its lines wait in Failed until you pick a section or Skip.')
        if re.match(r'入库', text):
            problem += (' 入库（新货直接进楼上）日报不移库存：用 入店 Receive goods 或 盘点 楼上 记，'
                        '这段选 Skip。入库 is not moved by the report: record it upstairs, then Skip.')
        out.append(_check('fix', [line, *under], problem))
    seen = defaultdict(list)
    for line, section, text in headings:
        if section not in ('unknown', 'cash_total'):
            # By the heading as written: "出库入display" and "入display" are two parts staff meant apart.
            seen[(section, re.sub(r'[\s:：]|汇总', '', text.casefold()))].append(line)
    for (section, _), lines in seen.items():
        if len(lines) > 1 and section in STOCK_LINES:
            out.append(_check('check', lines, f'这个标题出现 {len(lines)} 次：每段都会移库存，确认没有重复写。'
                                              f'Heading written {len(lines)} times: every part moves stock.', section))
    twice = defaultdict(list)
    for item in items:
        if item['section'] in STOCK_LINES and item['qty']:
            # The note is part of the line: "shelter*1（Circus）" and "shelter*1（Mantel Clock）" are two designs.
            twice[(item['section'], _key(item['raw_name']), _key(item.get('note')), item.get('box_size'), item['qty'],
                   item.get('loose_qty'), bool(item.get('to_display')), bool(item.get('from_display')))].append(item)
    for (section, *_), same in twice.items():
        lines = [item['line'] for item in same]
        if len(same) > 1 and len(set(lines)) > 1:
            out.append(_check('check', lines, f'「{same[0]["raw_name"]}」写了 {len(same)} 次：会移 {len(same)} 次。'
                                              f'Written {len(same)} times: it moves {len(same)} times.', section))
    for item in items:
        name, section, line = item['raw_name'], item['section'], item['line']
        if item.get('flagged') and not item['qty'] and name:
            source = _source(item)
            if _TIMES.search(source):
                why = f'「{name}」的数量要写成 *N，不是 x N：现在读成 0。Write *N, not xN: read as 0.'
            elif re.search(r'\*\s*0+\s*$', source):
                why = f'「{name}」数量是 0。Quantity 0.'
            elif section in ('refill', 'stock_in') and '*' in source:
                why = f'「{name}」读不出每端几个和几端：现在读成 0。Cannot read the set size and count: read as 0.'
            elif re.fullmatch(r'[㐀-鿿]{2,6}', name):
                # "入库" with no colon: a heading POPCORE reads as an item, so the lines below it join the
                # section above.
                why = (f'「{name}」没有数量（*N），读成 0。如果它是一个标题，后面要加冒号「{name}：」，否则它下面的行'
                       f'算在「{SECTION_LABELS.get(section, section)}」里。No quantity; if it is a heading, add a colon, '
                       'or the lines below it are read as the section above.')
            else:
                why = f'「{name}」没有数量（*N）：读成 0。No quantity: read as 0.'
            out.append(_check('fix', [line], f'{why} {_NO_SAVE}', section))
        elif section in SALES and item['qty'] >= BIG_SALE:
            out.append(_check('check', [line], f'一行卖了 {item["qty"]} 个「{name}」：确认不是打错。'
                                               f'One line sells {item["qty"]}: check it is not a typo.', section))
        if item.get('name_number'):
            number = item['name_number']
            out.append(_check('check', [line], f'「{name}*{item["qty"]}」：{number} 读成名字的一部分，出店 {item["qty"]} 个。'
                                               f'如果是 {item["qty"]} 端每端 {number} 个，写成 *{number * item["qty"]}。'
                                               f'{number} is read as part of the name.', section))
        if section in SALES and item.get('qty_assumed'):
            # "小王子-Rose- 出库入店" (DT, 2026-11-07): a place mark but no *N.
            out.append(_check('check', [line], f'「{name}」没写数量（*N）：按 1 个读。No quantity written: read as 1.', section))
        if section in SALES and item.get('moved_mark'):
            mark, at, place = item['moved_mark']
            where = '从 display 卖的（卖display）' if place == 'display' else '从楼上拿下来的（出库入店）'
            out.append(_check('check', [line, at], f'「{mark}」读成前面「{name}」的地方：{where}，不是另一个产品。'
                                                   'Read as where the item before it came from.', section))
    return out


def repeated_display_checks(blocks, items):
    """A 卖display section left out because every line in it repeats a sale already marked (display) (MK Nov 6,
    DT and MK Nov 8): saying it explicitly, by its lines, so it is never a silent change."""
    out = []
    for block in blocks:
        lines = [item['line'] for item in items if item.get('display_block') == block]
        out.append(_check('check', [block, *lines], f'这段 卖display 的 {len(lines)} 行，销售里都已标（display）：已不算，'
                                                    '陈列只减一次。如果确实是另外从 display 卖的，先在销售里补上那笔销售。'
                                                    'Every line repeats a (display) sale: left out, so display is '
                                                    'taken once.', 'display_sold'))
    return out


def header_checks(header_line, detected_date, detected_store, page_date, page_store, first_line=None):
    """The report's own date and store against the day and store the manager opened: a paste saves to
    what the report says, replacing that day's report. `first_line` is (number, text) of the paste's first
    line, where a date written without its year ("11.5 太谷", MK 2026-11-05) is read as a product."""
    out = []
    where = [header_line] if header_line else []
    if page_date and detected_date and detected_date != page_date:
        out.append(_check('fix', where, f'报告写的是 {detected_date}，你打开的是 {page_date}：会保存到 {detected_date}，'
                                        f'替换那天的日报。The report says {detected_date}: it saves there.'))
    elif not detected_date and first_line and re.match(r'\d{1,2}\s*[.\-/月]\s*\d{1,2}(?!\d)', first_line[1]):
        out.append(_check('fix', [first_line[0]], f'「{first_line[1]}」像是日期，但没有年份：读成了一个产品，日期按你打开的'
                                                  f'{" " + page_date if page_date else "那天"}。写成「2026.11.5 太古汇总」这样。'
                                                  'A date without its year is read as a product line.'))
    elif page_date and not detected_date:
        out.append(_check('check', [], f'没有找到日期行（例如「2026.11.4 DT」）：会保存到你打开的 {page_date}。'
                                       f'No date line: it saves to {page_date}.'))
    if page_store and detected_store and detected_store != page_store:
        out.append(_check('fix', where, f'报告写的是 {detected_store}，你选的是 {page_store}：会保存到 {detected_store}。'
                                        f'The report says {detected_store}: it saves there.'))
    return out


def _name(product):
    return product['jizhanming'] or product['name_cn_en'] or product['sku']


def product_checks(con, rows, unmatched=()):
    """Checks that need the products the paste matched (a live store's stock lines): set sizes against
    what POPCORE records, 出库入店 written as boxes where a whole set exists, and 卖display against the
    day's sales. They follow the paste's own match, so a row picked differently may settle them. A 卖display
    line that matched nothing is checked by its written name against the sales lines."""
    out = []
    written = {_key(row['raw_name']) for row in [*rows, *unmatched] if row.get('section') in SALES}
    for row in unmatched:
        if row.get('line') and row.get('section') == 'display_sold' and _key(row['raw_name']) not in written:
            out.append(_check('check', [row['line']], f'卖display「{row["raw_name"]}」在 卡机/随手记 里没有同名的销售：陈列会少 '
                                                      f'{row["qty"]}，但可能漏记了这笔销售。Sold from display, but no sales '
                                                      'line has this name.', 'display_sold'))
    rows = [row for row in rows if row.get('line') and (row.get('product') or {}).get('id')]
    ids = sorted({row['product']['id'] for row in rows})
    if not ids:
        return out
    products = {row['id']: row for row in con.execute(
        f'SELECT * FROM products WHERE id IN ({",".join("?" * len(ids))})', ids)}
    for row in rows:
        product = products.get(row['product']['id'])
        if product is None or row['section'] not in ('refill', 'stock_in') or row.get('to_display'):
            continue
        size, sets, name = row.get('box_size') or 1, row['qty'], _name(product)
        if product['stock_form'] == 'sealed_set':
            per = _stock_product(con, product)[1]
            if per is None:
                out.append(_check('fix', [row['line']], f'POPCORE 没记「{name}」每端几个：确认时会被拒绝，先在 产品 Products 记上。'
                                                        'Boxes per set never recorded: confirming refuses it.', row['section']))
            elif size not in (1, per):
                out.append(_check('fix', [row['line']], f'写的是每端 {size} 个，POPCORE 记「{name}」每端 {per} 个：'
                                                        f'确认时会被拒绝。The report says {size} per set, POPCORE {per}.',
                                  row['section']))
            continue
        whole = _set_for_box(con, product['id']) if product['stock_form'] == 'random_box' else None
        if whole is None or not whole['boxes_per_set']:
            continue
        per = whole['boxes_per_set']
        if size > 1 and size != per:
            out.append(_check('fix', [row['line']], f'写的是每端 {size} 个，POPCORE 记「{name}」每端 {per} 个：'
                                                    f'确认时会被拒绝。The report says {size} per set, POPCORE {per}.',
                              row['section']))
        elif size == 1 and row['section'] == 'refill' and not row.get('loose_qty') and not row.get('sale_mark'):
            out.append(_check('check', [row['line']], f'没写每端几个：按 {sets} 个散盒从楼上拿下来，楼上的整端不拆。'
                                                       f'如果是 {sets} 端，写成「{row["raw_name"]} {per}*{sets}」。'
                                                       f'Read as {sets} loose boxes, not sets of {per}.', row['section']))
    # A design sold from display may be written as its series box among the sales, or the other way round
    # ("海贼王船 - Polar Tang" on 卖display, "海贼王船*1（Polar Tang）" sold, DT 2026-11-08): the stock relief
    # counts them together by series, and so does this check.
    def series(product_id):
        product = products[product_id]
        return ('series', product['series_id']) if product['series_id'] is not None else ('product', product_id)

    shown, sold = defaultdict(list), defaultdict(list)
    for row in rows:
        if row['section'] == 'display_sold':
            shown[series(row['product']['id'])].append(row)
        elif row['section'] in SALES:
            sold[series(row['product']['id'])].append(row)
    for group, lines in shown.items():
        display, sales = sum(row['qty'] for row in lines), sum(row['qty'] for row in sold.get(group, []))
        if display > sales:
            name = _name(products[lines[0]['product']['id']])
            out.append(_check('check', [row['line'] for row in lines + sold.get(group, [])],
                              f'卖display 写了 {display} 个「{name}」，销售里只有 {sales} 个（同系列一起算）：陈列少 '
                              f'{display}，销售只记 {sales}。按现在的匹配。Display sold {display}, sales list {sales}.',
                              'display_sold'))
    return out
