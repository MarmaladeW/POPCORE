"""Privacy boundary for optional error telemetry."""


# x-popcore-person was the store computer's name + PIN header. Nothing reads it since 2026-11-26, but a page loaded
# before that still sends a PIN in it, so it never goes into an error report.
SENSITIVE_HEADERS = {'authorization', 'cookie', 'set-cookie', 'x-api-key', 'x-popcore-person'}
# Code that holds customers' names read live from the website (web orders board, owner 2026-11-25): an error report
# never carries its local variables.
PRIVATE_MODULES = {'website_board', 'blueprints.website'}


def scrub_event(event, _hint):
    event.pop('user', None)
    request = event.get('request')
    if isinstance(request, dict):
        request.pop('data', None)
        request.pop('query_string', None)
        headers = request.get('headers')
        if isinstance(headers, dict):
            request['headers'] = {
                key: value for key, value in headers.items()
                if key.lower() not in SENSITIVE_HEADERS
            }
    for exception in (event.get('exception') or {}).get('values') or ():
        for frame in (exception.get('stacktrace') or {}).get('frames') or ():
            if frame.get('module') in PRIVATE_MODULES:
                frame.pop('vars', None)
    breadcrumbs = event.get('breadcrumbs', {}).get('values', [])
    for breadcrumb in breadcrumbs:
        if breadcrumb.get('category') in {'http', 'httplib', 'requests'}:
            breadcrumb.pop('data', None)
    return event
