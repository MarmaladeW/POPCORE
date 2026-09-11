"""Privacy boundary for optional error telemetry."""


SENSITIVE_HEADERS = {'authorization', 'cookie', 'set-cookie', 'x-api-key'}


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
    breadcrumbs = event.get('breadcrumbs', {}).get('values', [])
    for breadcrumb in breadcrumbs:
        if breadcrumb.get('category') in {'http', 'httplib', 'requests'}:
            breadcrumb.pop('data', None)
    return event
