from pathlib import Path

from archivebox.config import VERSION
from archivebox.config.version import get_COMMIT_HASH


def get_static_cache_key() -> str:
    """Version the admin stylesheet even when the checkout has uncommitted edits."""
    base_key = (get_COMMIT_HASH() or VERSION or "dev").strip()
    admin_css_path = Path(__file__).resolve().parent.parent / "templates" / "static" / "admin.css"
    try:
        return f"{base_key}-{admin_css_path.stat().st_mtime_ns}"
    except OSError:
        return base_key


def archivebox_globals(request):
    from archivebox.core.user_scope import scope_summary

    return {
        "VERSION": VERSION,
        "STATIC_CACHE_KEY": get_static_cache_key(),
        # Which user's items this request can see, for the banner in admin/base.html.
        # The filter itself is enforced in the querysets, never here.
        "USER_SCOPE": scope_summary(request),
    }
