"""Shared helper for rendering a user/synced description link in a QLabel.

The raw link used to be interpolated into rich-text HTML unescaped (markup
injection / spoofed link text) and any scheme (file:, etc.) was handed to
QDesktopServices.openUrl on click. Only http(s) links become clickable, and
the text/href are always HTML-escaped."""
import html
from urllib.parse import urlparse

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel


def is_safe_http_url(url):
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def open_safe_url(url):
    if is_safe_http_url(url):
        QDesktopServices.openUrl(QUrl(url))


def make_link_label(link):
    safe = html.escape(link, quote=True)
    if is_safe_http_url(link):
        label = QLabel(f'<a style="color: #1F2328;" href="{safe}">{safe}</a>')
    else:
        label = QLabel(safe)
    label.setOpenExternalLinks(False)
    label.linkActivated.connect(open_safe_url)
    return label
