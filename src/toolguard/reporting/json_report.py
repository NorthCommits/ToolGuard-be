"""JSON report rendering. Input is a report already passed through reporting.safe.neutralize."""

import json

from toolguard.core.models import ScanReport

# Bump when a field is removed or changes meaning; adding a field does not need a bump.
REPORT_SCHEMA_VERSION = "1"

# With ensure_ascii, these can only occur inside strings, so escaping them is lossless and
# keeps the JSON safe to embed in an HTML page.
_HTML_ESCAPES = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026"}


def render(report: ScanReport) -> str:
    text = json.dumps(report.model_dump(mode="json"), sort_keys=True, ensure_ascii=True, indent=2)
    for char, escape in _HTML_ESCAPES.items():
        text = text.replace(char, escape)
    return text + "\n"
