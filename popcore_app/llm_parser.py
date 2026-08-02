"""
llm_parser.py — LLM front-end for the daily sales report import.

Turns a free-form end-of-day report (mixed Chinese/English, arbitrary worker
formatting habits) into the structured item list the import pipeline consumes.
The model does NOT identify products — it only reads the report the way a
coworker would; product resolution stays in matcher.py + product_aliases.

Enabled when ANTHROPIC_API_KEY is set; callers must treat any failure as
"fall back to the rule parser". Uses the Anthropic Messages API directly via
requests (already a project dependency) with a forced tool call so the output
is schema-validated JSON, not prose.
"""
import os
import re
import requests

_API_URL = 'https://api.anthropic.com/v1/messages'
_MODEL_DEFAULT = 'claude-haiku-4-5-20251001'

_SECTIONS = ['pos', 'cash', 'claw', 'sell_display', 'break_display',
             'employee_discount', 'stock_in', 'stock_out', 'skip', 'unknown']

_DATE_FMT_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_STORE_RE    = re.compile(r'^[A-Za-z]{2,6}$')

_TOOL = {
    'name': 'record_daily_report',
    'description': 'Record the structured contents of one pasted end-of-day sales report.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'date': {
                'type': ['string', 'null'],
                'description': 'Report date as YYYY-MM-DD, null if no date is written.',
            },
            'store': {
                'type': ['string', 'null'],
                'description': 'Store code letters if written (e.g. "DT", "MK"), else null.',
            },
            'cash_total': {
                'type': ['number', 'null'],
                'description': 'The reported cash/现金 total amount if a 现金 total line exists, else null.',
            },
            'extra_dates': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'Any ADDITIONAL dates found beyond the first (multi-day paste detection).',
            },
            'items': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'section': {
                            'type': 'string',
                            'enum': _SECTIONS,
                            'description': 'Which report section the item belongs to.',
                        },
                        'name': {
                            'type': 'string',
                            'description': 'Product name EXACTLY as written, minus quantity/notes. Never translate, expand, or correct it.',
                        },
                        'qty': {
                            'type': ['integer', 'null'],
                            'description': 'Quantity if written (the N in *N). null when no quantity is written — do NOT assume 1.',
                        },
                        'note': {
                            'type': 'string',
                            'description': 'Parenthetical or trailing annotation for this item, e.g. 去太古. Empty string if none.',
                        },
                        'box_size': {
                            'type': ['integer', 'null'],
                            'description': 'For stock_in (入店) items written as "name S*N": S = units per box/display. null otherwise.',
                        },
                        'header_text': {
                            'type': 'string',
                            'description': 'For section=unknown only: the unrecognized section header this item sits under. Empty otherwise.',
                        },
                    },
                    'required': ['section', 'name'],
                },
            },
        },
        'required': ['items'],
    },
}

_SYSTEM = """你在为一家潮玩零售店解析每日交班报告。报告由不同员工手写粘贴，格式随意：
分区标题可能带冒号也可能不带，一行可能有多个商品（逗号分隔），数量写作 名称*数量。

分区词汇 → section 值：
- 卡机 / 卡机汇总（刷卡机销售）→ pos
- 随手记 / 随手记汇总（现金或转账销售）→ cash
- 娃娃机（claw machine 销售）→ claw
- 卖display / 卖展示（卖出展示品）→ sell_display
- 拆display / 入display（拆开或放入展示，非销售）→ break_display
- 员工折扣 → employee_discount
- 入店（从楼上仓库进店补货，非销售；"名称 6*2" 表示 每箱6个×2箱，box_size=6, qty=2）→ stock_in
- 出店（调货去其他门店，非销售）→ stock_out
- 晚盘 / 博主探店 及其下的内容 → skip
- 现金总额行（如 现金：$300）不是商品：把金额填入 cash_total
- 无法归类的分区：section=unknown，并把该分区标题原样填入 header_text

规则：
1. 逐行提取，不要发明、合并或纠正商品名 — name 必须原样保留员工写的字符。
2. 没写数量就把 qty 设为 null，绝不要默认为 1。
3. 括号内容是备注（note），不属于商品名。
4. 第一行通常是日期和门店（如 2026.04.01 DT汇总）— 提取到 date/store，不要当作商品。
5. 发现多个日期时，第一个填 date，其余全部列入 extra_dates。
6. 分区标题行本身不是商品；标题后同一行的内容（出店：xxx*1）是该分区的商品。"""


def available() -> bool:
    return bool(os.environ.get('ANTHROPIC_API_KEY'))


def _validate(payload: dict) -> dict:
    """Clamp the model output to the shapes the pipeline trusts."""
    out: dict = {'date': None, 'store': None, 'cash_total': None,
                 'extra_dates': [], 'items': []}

    d = payload.get('date')
    if isinstance(d, str) and _DATE_FMT_RE.match(d.strip()):
        out['date'] = d.strip()

    s = payload.get('store')
    if isinstance(s, str) and _STORE_RE.match(s.strip()):
        out['store'] = s.strip().upper()

    ct = payload.get('cash_total')
    if isinstance(ct, (int, float)) and ct >= 0:
        out['cash_total'] = float(ct)

    for ed in payload.get('extra_dates') or []:
        if isinstance(ed, str) and _DATE_FMT_RE.match(ed.strip()):
            out['extra_dates'].append(ed.strip())

    for it in payload.get('items') or []:
        if not isinstance(it, dict):
            continue
        name = (it.get('name') or '').strip() if isinstance(it.get('name'), str) else ''
        if not name:
            continue
        section = it.get('section')
        if section not in _SECTIONS:
            section = 'unknown'
        qty = it.get('qty')
        qty = int(qty) if isinstance(qty, (int, float)) and int(qty) > 0 else None
        box = it.get('box_size')
        box = int(box) if isinstance(box, (int, float)) and int(box) > 0 else None
        note = it.get('note') if isinstance(it.get('note'), str) else ''
        hdr  = it.get('header_text') if isinstance(it.get('header_text'), str) else ''
        out['items'].append({
            'section':     section,
            'name':        name,
            'qty':         qty,
            'note':        note.strip(),
            'box_size':    box,
            'header_text': hdr.strip(),
        })
    return out


def parse_report_llm(raw_text: str, timeout: int = 45) -> dict | None:
    """Parse a report with the LLM. Returns a validated dict, or None when the
    model produced no usable tool call. Raises on transport/API errors —
    callers fall back to the rule parser."""
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        return None

    resp = requests.post(
        _API_URL,
        headers={
            'x-api-key':         key,
            'anthropic-version': '2023-06-01',
            'content-type':      'application/json',
        },
        json={
            'model':       os.environ.get('LLM_PARSER_MODEL', _MODEL_DEFAULT),
            'max_tokens':  4096,
            'system':      _SYSTEM,
            'tools':       [_TOOL],
            'tool_choice': {'type': 'tool', 'name': 'record_daily_report'},
            'messages':    [{'role': 'user', 'content': raw_text}],
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    for block in data.get('content', []):
        if block.get('type') == 'tool_use' and block.get('name') == 'record_daily_report':
            validated = _validate(block.get('input') or {})
            return validated if validated['items'] else None
    return None
