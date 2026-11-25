"""Format checks on a pasted daily report (owner, 2026-11-06): anything written in a way that may move
stock wrongly is pointed out by its line in the paste, quoting that line, with what POPCORE will do with
it. `fix` is most likely a mistake; `check` may be meant. A check never stops the save by itself: the rows
it points at are still accepted, corrected or removed on the paste screen as before."""
import re

from inventory_commands import InventoryError
from collections import defaultdict
from datetime import date, timedelta

from report_stock import (LINE_SECTIONS, PACKED_SECTIONS, ReportLineError, _line_moves, _locations, _move_key,
                          _set_for_box, _stock_product, app_moves, can_have_set, other_store)

SECTION_LABELS = {
    'pos': '卡机', 'cash': '随手记', 'refill': '出库入店', 'stock_in': '入店', 'stock_out': '出店',
    'display_sold': '卖display', 'break_display': '拆display', 'display_stock_in': '入display',
    'claw_prize': '娃娃机', 'store_transfer': '出库', 'claw_stock_in': '入娃娃机',
    'back_in': '入库', 'transfer_out': '出库', 'floor_to_back': '出店入库', 'display_to_floor': '出display入店',
}
SALES = ('pos', 'cash')
STOCK_LINES = tuple(LINE_SECTIONS)
# What a bare "*N" of a blind box with a whole set is read as, by section: N loose boxes, never N sets.
_LOOSE_READING = {
    'refill': ('从楼上拿下来，楼上的整端不拆', 'loose boxes'),
    'back_in': ('入库，不算整端', 'loose boxes into upstairs'),
    'transfer_out': ('送去另一家店，这里的整端不动', 'loose boxes to the other store'),
}
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
            elif section in PACKED_SECTIONS and '*' in source:
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


def old_reading_check(lines, since):
    """A report dated before the paste began moving 入库, 出库, 出店入库, 出display入店 and 出店display: those
    were recorded by hand then, so it reads them the old way and moves none of them."""
    return _check('check', lines, f'这份日报的日期早于 {since}（日报开始自己移 入库、出库、出店入库、出display入店、出店display 的那天）：'
                                  f'这些当时已经用 盘点 或 调拨 记过，所以还按以前的办法读，不移库存。'
                                  f'Dated before {since}: these sections were recorded by hand then, so they are '
                                  'read the old way and move no stock.')


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


# How long after the other store's 出库 an arrival written here is likely the same goods.
SENT_DAYS = 3
_PLACES = {'floor': '楼下 floor', 'upstairs': '楼上 upstairs', 'warehouse': '仓库 warehouse'}


def sent_here_checks(con, rows, store_id, day):
    """出库 puts goods onto the other store at once (owner, 2026-11-13), so the receiving store's own report
    must not add them a second time: an 入库 or 入店 line of something the other store's 出库 put here in the
    last days is pointed out, with what to do if it is the same goods."""
    try:
        since = (date.fromisoformat(day or '') - timedelta(days=SENT_DAYS)).isoformat()
    except ValueError:
        return []

    def box_of(product):
        return _stock_product(con, product)[0]['id'] if product['stock_form'] == 'sealed_set' else product['id']

    sent = defaultdict(list)
    for move in con.execute(
            """SELECT m.business_date, m.moved * m.ratio AS quantity, s.code AS sender, l.code AS place, p.*
               FROM report_stock_moves m JOIN stores s ON s.id=m.store_id
               JOIN inventory_locations l ON l.id=m.to_location_id JOIN products p ON p.id=m.to_product_id
               WHERE m.section='transfer_out' AND m.moved>0 AND l.store_id=? AND m.business_date BETWEEN ? AND ?
               ORDER BY m.business_date, m.rowid""", (store_id, since, day)):
        sent[box_of(move)].append(move)
    out = []
    for row in rows:
        if not row.get('line') or row.get('section') not in ('back_in', 'stock_in') or not (row.get('product') or {}).get('id'):
            continue
        product = con.execute('SELECT * FROM products WHERE id=?', (row['product']['id'],)).fetchone()
        for move in sent.get(box_of(product), []) if product else []:
            on_floor = move['place'] == 'floor'
            fix = ('移除这一行' if row['section'] == 'back_in' or on_floor
                   else '把这一行的分区改成 出库入店（货已经在这家店的楼上/仓库）')
            out.append(_check('check', [row['line']],
                              f"{move['sender']} {move['business_date']} 的 出库 已经把 {move['quantity']} "
                              f"{move['stock_unit']}「{_name(move)}」加到这家店的{_PLACES.get(move['place'], move['place'])}。"
                              f'如果这一行就是那批货，{fix}，不然会加两次。'
                              f"{move['sender']}'s 出库 on {move['business_date']} already put these here: if this "
                              'line is the same goods, it would count them twice.', row['section']))
    return out


def app_checks(con, rows, store_id, day):
    """A line whose goods an app screen already recorded that day (owner, 2026-11-20: 补货, 调拨, 收货, display,
    娃娃机) moves only what goes beyond that record, so staff can write the 日报 as always. Each such line says
    who recorded what, and the manager can mark it "这是另外的 It's extra" to move it in full."""
    recorded = app_moves(con, store_id, day)
    if not recorded:
        return []
    at = _locations(con, store_id)
    back, here, other = at.get('upstairs') or at.get('warehouse'), set(at.values()), other_store(con, store_id)
    out = []
    for row in rows:
        if not row.get('line') or row.get('section') not in LINE_SECTIONS or not (row.get('product') or {}).get('id'):
            continue
        product = con.execute('SELECT * FROM products WHERE id=?', (row['product']['id'],)).fetchone()
        item = {'section': row['section'], 'box_size': row.get('box_size') or 1, 'num_boxes': row.get('qty') or 0,
                'loose_qty': row.get('loose_qty') or 0, 'qty': row.get('qty') or 0, 'to_display': row.get('to_display'),
                'from_display': row.get('from_display'), 'to_floor': row.get('to_floor')}
        try:
            moves = _line_moves(con, row['line'], item, product, at, back, other) if product else []
        except (ReportLineError, InventoryError):
            continue
        found = [recorded[key] for key in (_move_key(kind, source, source_at, target, target_at, here)[0]
                                           for kind, source, source_at, target, target_at, _, _ in moves)
                 if key in recorded]
        if not found:
            continue
        units = sum(entry[0] for entry in found)
        who = '、'.join(sorted({label for entry in found for label in entry[1]}))
        check = _check('check', [row['line']],
                       f'app 里已经记了 {who}（{units} 个「{_name(product)}」）：这一行只移超出的部分，不会加两次；'
                       f'如果是另外一批，选「这是另外的」。Already recorded in the app ({who}): this line moves only what goes '
                       "beyond that. If it is another batch, choose It's extra.", row['section'])
        check['extra_line'] = row['line']
        out.append(check)
    return out


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
        if product is None or row['section'] not in PACKED_SECTIONS or row.get('to_display'):
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
            if row['section'] == 'back_in' and size > 1 and can_have_set(con, product):
                # 入库 "hellokitty苹果贴贴12*8" (DT, 2026-11-11): the first whole sets of a blind box.
                known = product['boxes_per_dan'] if (product['boxes_per_dan'] or 0) > 1 else None
                differs = f'产品资料写每端 {known} 个，' if known and known != size else ''
                out.append(_check('check', [row['line']],
                                  f'「{name}」还没有整端：保存时会新建它的整端（每端 {size} 个），入库 {sets} 端。{differs}'
                                  f'如果 {size} 不是每端的数量，先改这一行。Its whole set does not exist yet: saving adds '
                                  f'it with {size} boxes per set.', row['section']))
            continue
        per = whole['boxes_per_set']
        if size > 1 and size != per:
            out.append(_check('fix', [row['line']], f'写的是每端 {size} 个，POPCORE 记「{name}」每端 {per} 个：'
                                                    f'确认时会被拒绝。The report says {size} per set, POPCORE {per}.',
                              row['section']))
        elif size == 1 and row['section'] in _LOOSE_READING and not row.get('loose_qty') and not row.get('sale_mark'):
            does, english = _LOOSE_READING[row['section']]
            out.append(_check('check', [row['line']], f'没写每端几个：按 {sets} 个散盒{does}。'
                                                       f'如果是 {sets} 端，写成「{row["raw_name"]} {per}*{sets}」。'
                                                       f'Read as {sets} {english}, not sets of {per}.', row['section']))
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


# ── Repeated blocks (owner, 2026-11-15) ─────────────────────────────────────
# DT's Nov 14 入库 repeated a five-line block by a copy/paste slip; pasted raw, the goods would have been added
# twice without a word. Stock sections only: in 卡机/随手记 one line is one sale, so the same line twice is normal.
_PUNCTUATION = str.maketrans({'，': ',', '：': ':', '；': ';', '（': '(', '）': ')', '、': ',', '。': '.', '＊': '*',
                              '×': '*', '【': '[', '】': ']', '“': '"', '”': '"', '‘': "'", '’': "'"})


def same_text(text):
    """A line as compared for a repeat: spacing, case and full- or half-width punctuation do not count."""
    import unicodedata
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text or '').translate(_PUNCTUATION).casefold())


def _span(lines):
    return f'第 {lines[0]} 行' if len(lines) == 1 else f'第 {lines[0]}–{lines[-1]} 行'


def _en_span(lines):
    return f'line {lines[0]}' if len(lines) == 1 else f'lines {lines[0]}–{lines[-1]}'


def repeated_blocks(parts):
    """Where a stock section repeats itself word for word: a heading written again with the same lines
    ('section'), or a run of two or more lines written again later in the same section ('block'). `parts` are
    the stock sections as written: (heading line, section, [(line, text), ...]), in paste order. A single line
    written twice is not a block: it may be meant (two sets), so text_checks only asks about it.
    Returns each repeat with its id, the copy's lines (heading included) and the lines it repeats."""
    repeats, copied = [], set()
    by_section = defaultdict(list)
    for heading, section, lines in parts:
        lines = [(line, same_text(text)) for line, text in lines if same_text(text)]
        for earlier_heading, earlier in by_section[section]:
            if lines and [text for _, text in earlier] == [text for _, text in lines]:
                copy = [heading] + [line for line, _ in lines]
                repeats.append({'id': f'section:{heading}', 'kind': 'section', 'section': section, 'lines': copy,
                                'of': [earlier_heading] + [line for line, _ in earlier]})
                copied.update(copy)
                break
        else:
            by_section[section].append((heading, lines))
    for section, groups in by_section.items():
        seq = [(line, text) for _, lines in groups for line, text in lines if line not in copied]
        j = 0
        while j < len(seq):
            best, start = 0, None
            for i in range(j):
                k = 0
                while j + k < len(seq) and i + k < j and seq[i + k][1] == seq[j + k][1]:
                    k += 1
                if k > best:
                    best, start = k, i
            if best >= 2:
                copy = [line for line, _ in seq[j:j + best]]
                repeats.append({'id': f'block:{copy[0]}-{copy[-1]}', 'kind': 'block', 'section': section, 'lines': copy,
                                'of': [line for line, _ in seq[start:start + best]]})
                j += best
            else:
                j += 1
    repeats.sort(key=lambda repeat: repeat['lines'][0])
    return repeats


def repeat_check(repeat):
    """The format check of one repeat, with the manager's choice: drop the repeat (default) or count both."""
    copy, of = repeat['lines'], repeat['of']
    what = '这个标题和下面的行' if repeat['kind'] == 'section' else f'这 {len(copy)} 行'
    check = _check('check', [*of, *copy],
                   f'{_span(copy)}：{what}和{_span(of)}一字不差，多半是复制粘贴重复了。默认「去掉重复」只算一次；'
                   f'如果真的是两批货，选「两次都算」。{_en_span(copy).capitalize()} repeat {_en_span(of)} word for word: '
                   'the repeat is dropped unless you choose Count both.', repeat['section'])
    check['repeat_id'] = repeat['id']
    check['repeat_lines'] = copy
    return check


def report_twice_check(line, first):
    """The whole report pasted twice into the box (same date header twice, the same lines after it)."""
    return _check('fix', [first, line], f'整份日报贴了两次：第 {line} 行起和第 {first} 行起一模一样，只读第一份。'
                                        f'The whole report is pasted twice (from line {line}): only the first copy is read.')
