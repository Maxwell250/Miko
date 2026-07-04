from __future__ import annotations

import html


def escape_html(text: object) -> str:
    """Экранирование для Telegram parse_mode=HTML."""
    if text is None:
        return ""
    return html.escape(str(text), quote=False)
