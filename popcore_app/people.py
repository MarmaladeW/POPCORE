"""Who an account is, by name, wherever the app shows who did something (owner, 2026-11-20: "in WeChat it is super
easy to check who did what"). An account with no staff profile shows as its own kind, never as a raw login id."""

SYSTEM_NAMES = {
    'popcore:website': '网站 Website',
    'popcore:clover-register': 'Clover register',
}


# Never sent to a page (owner, 2026-11-26): a PIN's hash and lock state (empty since 2026-11-26, store_computer.py; four digits are found from the
# hash at once), and the private calendar address, which only GET /api/schedule/calendar-feed gives, to its owner.
PRIVATE_FIELDS = ('pin_hash', 'pin_salt', 'pin_failures', 'pin_locked_until', 'pin_set_at', 'ical_token')


def public(row):
    """An employees row as a page may have it."""
    return {key: value for key, value in dict(row).items() if key not in PRIVATE_FIELDS}


def names(con, subjects):
    """{account: display name} for these accounts."""
    subjects = sorted({subject for subject in subjects if subject})
    found = {subject: SYSTEM_NAMES[subject] for subject in subjects if subject in SYSTEM_NAMES}
    rest = [subject for subject in subjects if subject not in found]
    if rest:
        marks = ','.join('?' * len(rest))
        found.update({row[0]: row[1] for row in con.execute(
            f'SELECT auth0_id, name FROM employees WHERE auth0_id IN ({marks})', rest)})
    return {subject: found.get(subject) or ('POPCORE' if subject.startswith('popcore:') else '没有员工资料 No staff profile')
            for subject in subjects}


def name(con, subject):
    return names(con, [subject]).get(subject) if subject else None


def with_names(con, rows, *fields):
    """Each row (a dict) gains `<field>_name` beside every account field named."""
    found = names(con, [row.get(field) for row in rows for field in fields])
    for row in rows:
        for field in fields:
            row[f'{field}_name'] = found.get(row.get(field))
    return rows
