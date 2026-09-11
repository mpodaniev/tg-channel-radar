Saved HTML pages from Telegram's anonymous web preview (`t.me/s/<channel>`), used by
`tests/test_parser.py` so parser tests never touch the network.

Fetched 2026-09-11 with:

```
curl -s -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 \
  (KHTML, like Gecko) Chrome/124.0 Safari/537.36" "https://t.me/s/<channel>"
```

| File | Source | Notes |
|---|---|---|
| `channel_basic.html` | `t.me/s/durov` | Mostly text posts, real reactions (incl. a "stars" paid reaction), views, dates. |
| `channel_media.html` | `t.me/s/telegram` | Photo/video posts and link previews; has a `.tme_messages_more` pagination cursor. |
| `channel_paginated.html` | `t.me/s/durov?before=100` | A second page fetched via the `before` cursor, has its own further `data-before`. |
| `channel_not_found.html` | `t.me/s/<nonexistent-username>`, redirect followed | `t.me/s/<username>` 302-redirects to the plain `t.me/<username>` "Contact" page for both nonexistent and preview-disabled channels; this is that landing page (HTTP 200, no `.tgme_widget_message` blocks). `app/parser/tme.py` never follows this redirect — it raises `ChannelNotFoundError` on the 302 itself — so this fixture only exercises `parse_channel_page`, not the network layer. |

None of the fetched channels expose a forward count in the web preview; `ParsedPost.forwards`
is always `None` from this source (see `app/parser/normalize.py`).
