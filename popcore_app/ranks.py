"""Staff ranks (owner, 2026-11-19): 店长, 副店长, 资深员工, 正式员工, 培训员工, kept on each person's employees row.

A rank also sets what the person may do in POPCORE: 店长 and 副店长 get the Auth0 role manager, the others staff.
查看者 Viewer (a look-only account, such as the store computer's) and 管理员 Admin have no rank and stay their own
roles; a viewer is never a 培训员工. Every list of people shows them in rank order, 店长 first, then by name.

培训员工 is the old 培训中 status (is_training) and 正式员工 and above are 转正 (regular_since, the day it started), so
the schedule's 训 and 正 marks and the monthly hours keep working from those two fields.
"""

# Top first.
RANKS = ('store_manager', 'assistant_manager', 'senior', 'regular', 'trainee')
RANK_LABELS = {'store_manager': '店长', 'assistant_manager': '副店长', 'senior': '资深员工', 'regular': '正式员工',
               'trainee': '培训员工'}
RANK_ROLE = {'store_manager': 'manager', 'assistant_manager': 'manager', 'senior': 'staff', 'regular': 'staff',
             'trainee': 'staff'}
# 正式员工 and above get 4 员工折扣 a month (owner, 2026-11-19).
DISCOUNT_RANKS = ('store_manager', 'assistant_manager', 'senior', 'regular')


def order_by(alias='e'):
    """An ORDER BY clause listing people by rank, 店长 first, people without a rank last, then by name."""
    column = f'{alias}.rank' if alias else 'rank'
    name = f'{alias}.name' if alias else 'name'
    whens = ' '.join(f"WHEN '{rank}' THEN {index}" for index, rank in enumerate(RANKS))
    return f'CASE {column} {whens} ELSE {len(RANKS)} END, {name} COLLATE NOCASE'
