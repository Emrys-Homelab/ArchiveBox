"""Per-user isolation: every snapshot belongs to exactly one ``user-<name>`` tag.

THE RULE. Every snapshot carries exactly one ownership tag of the form
``user-<username>``, written when the snapshot row is created and never rewritten
by a later tag edit. That tag is the ownership record; nothing else (no column, no
crawl relationship) decides who may see a snapshot.

WHO OWNS A NEW SNAPSHOT. Resolved once, at creation, in this order:

 1. the ``created_by`` user of the Crawl the snapshot belongs to - the web add form
    puts the session user there, and the API puts the *API token's owner* there, so
    a token minted by A attributes A's ingestion to A;
 2. ``ARCHIVEBOX_ADD_USER`` - the explicit identity a pod/CLI add supplies when it
    has no authenticated user (``archivebox add --user <name>`` sets it too);
 3. the owner of the parent snapshot, for links discovered during a crawl;
 4. otherwise the snapshot is left UNTAGGED, which means invisible to everyone.
    That is deliberate: it fails closed, and the CLI refuses to create unattributed
    work rather than producing invisible captures.

WHO MAY SEE WHAT. ``scoped_snapshot_queryset`` narrows a queryset to the request
user's own tag and ``can_access_snapshot`` applies the same rule to one object.
Both run on the server for every list, search, detail, download and API read, and
no query parameter widens them. Superusers are not exempt - the operator's own view
behaves like everyone else's - except for the deliberate "see everything" toggle: a
POST-only session action available to superusers (see ``SEE_ALL_SESSION_KEY``).

``user-*`` TAGS ARE INTERNAL. The ``Tag`` default manager excludes them, so they
never appear in a tag index, a picker, a snapshot card, an export or an API
response. Code that needs them goes through ``Tag.all_objects`` (this module only).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable

from django.http import Http404, HttpRequest

logger = logging.getLogger(__name__)

USER_TAG_PREFIX = "user-"

#: Explicit identity for adds that have no authenticated user (CronJob, pod shell).
ADD_USER_ENV = "ARCHIVEBOX_ADD_USER"

#: Session key for the superuser-only, POST-only "see every user's items" toggle.
SEE_ALL_SESSION_KEY = "archivebox_see_all"

#: Upstream's placeholder account for work nobody claimed. Never an owner.
SYSTEM_USERNAME = "system"

#: ``core_tag.name`` is a CharField(max_length=100); a longer name cannot exist.
TAG_NAME_MAX_LENGTH = 100


# ---------------------------------------------------------------------------
# Tag naming
# ---------------------------------------------------------------------------


def is_user_tag(name: object) -> bool:
    """True for an internal ownership tag (``user-<name>``)."""
    return str(name or "").strip().lower().startswith(USER_TAG_PREFIX)


def user_tag_name(username: object) -> str:
    """The tag name that records ownership by ``username``."""
    from archivebox.misc.util import sanitize_html_text

    return sanitize_html_text(f"{USER_TAG_PREFIX}{str(username or '').strip()}").strip()


def username_from_user_tag(name: object) -> str | None:
    """Inverse of :func:`user_tag_name`, or None when ``name`` is not an owner tag."""
    if not is_user_tag(name):
        return None
    return str(name).strip()[len(USER_TAG_PREFIX) :]


def tag_name_is_usable(username: object) -> bool:
    """Whether ``username`` can be recorded as an ownership tag.

    A name that would be truncated by the column limit is refused rather than
    silently collided with another user's tag.
    """
    name = user_tag_name(username)
    return bool(str(username or "").strip()) and len(name) <= TAG_NAME_MAX_LENGTH and username_from_user_tag(name) == str(username or "").strip()


# ---------------------------------------------------------------------------
# Reading the ownership of one snapshot
# ---------------------------------------------------------------------------


def snapshot_owner_names(snapshot: Any) -> list[str]:
    """Ownership usernames recorded on ``snapshot`` (normally exactly one).

    Reads the through table directly: ``snapshot.tags`` is the filtered manager and
    deliberately cannot see these rows.
    """
    from archivebox.core.models import SnapshotTag

    if snapshot is None or not getattr(snapshot, "pk", None):
        return []
    names = SnapshotTag.objects.filter(
        snapshot_id=snapshot.pk,
        tag__name__istartswith=USER_TAG_PREFIX,
    ).values_list("tag__name", flat=True)
    owners: list[str] = []
    for name in names:
        username = username_from_user_tag(name)
        if username and username not in owners:
            owners.append(username)
    return owners


def snapshot_owner(snapshot: Any) -> str | None:
    """The single ownership username of ``snapshot``, or None when untagged."""
    owners = snapshot_owner_names(snapshot)
    return owners[0] if owners else None


def is_unowned(snapshot: Any) -> bool:
    return snapshot is not None and not snapshot_owner_names(snapshot)


# ---------------------------------------------------------------------------
# Writing the ownership tag
# ---------------------------------------------------------------------------


def add_user_from_env() -> str | None:
    """The explicit add identity from the environment, if one is set."""
    username = str(os.environ.get(ADD_USER_ENV) or "").strip()
    if not username:
        return None
    if not tag_name_is_usable(username):
        raise ValueError(f"{ADD_USER_ENV}={username!r} cannot be recorded as a {USER_TAG_PREFIX} tag")
    return username


def resolve_owner_username(snapshot: Any) -> str | None:
    """Which username should own a snapshot that is being created.

    See the module docstring for the order and why it is the order.
    """
    crawl = None
    if getattr(snapshot, "crawl_id", None):
        crawl = getattr(snapshot, "crawl", None)
        created_by = getattr(crawl, "created_by", None) if crawl is not None else None
        username = str(getattr(created_by, "username", "") or "").strip()
        if username and username.lower() != SYSTEM_USERNAME:
            return username

    try:
        explicit = add_user_from_env()
    except ValueError:
        logger.error("user_scope: %s is not usable as an owner; leaving snapshot untagged", ADD_USER_ENV)
        explicit = None
    if explicit:
        return explicit

    parent = getattr(snapshot, "parent_snapshot", None)
    if parent is not None:
        owner = snapshot_owner(parent)
        if owner:
            return owner
    return None


def get_or_create_user_tag(username: str) -> Any:
    """The Tag row for ``username``, created on demand, bypassing the filter."""
    from archivebox.core.models import Tag

    name = user_tag_name(username)
    tag = Tag.all_objects.filter(name__iexact=name).first()
    if tag is not None:
        return tag
    tag, _created = Tag.all_objects.get_or_create(name=name)
    return tag


def stamp_snapshot(snapshot: Any) -> Any | None:
    """Record (or confirm) the ownership tag on ``snapshot``.

    Called from ``Snapshot.save_tags`` on creation, which is the one choke point
    every snapshot-creating path goes through. Idempotent, and never removes a tag
    it did not put there - except another ``user-*`` tag, because a snapshot with
    two owners would be visible to both and that is not a state this design has.
    """
    from archivebox.core.models import SnapshotTag

    if snapshot is None or not getattr(snapshot, "pk", None):
        return None

    existing = snapshot_owner_names(snapshot)
    if existing:
        # Ownership is written once: a tag edit, a re-crawl or an import must not
        # move a capture to a different user. Keep the first, drop any duplicates.
        owner = existing[0]
        tag = get_or_create_user_tag(owner)
        if len(existing) > 1:
            logger.warning(
                "user_scope: snapshot %s has %d ownership tags (%s); keeping %s",
                snapshot.pk,
                len(existing),
                ", ".join(existing),
                owner,
            )
            keep_name = user_tag_name(owner)
            SnapshotTag.objects.filter(snapshot_id=snapshot.pk, tag__name__istartswith=USER_TAG_PREFIX).exclude(tag_id=tag.pk).delete()
            logger.warning("user_scope: removed ownership tags other than %s from %s", keep_name, snapshot.pk)
        SnapshotTag.objects.get_or_create(snapshot_id=snapshot.pk, tag_id=tag.pk)
        return tag

    username = resolve_owner_username(snapshot)
    if not username:
        logger.warning(
            "user_scope: no owner for snapshot %s (crawl %s); it will be invisible to every user until one is assigned",
            snapshot.pk,
            getattr(snapshot, "crawl_id", None),
        )
        return None
    if not tag_name_is_usable(username):
        logger.error("user_scope: cannot record owner %r for snapshot %s; leaving it untagged", username, snapshot.pk)
        return None

    tag = get_or_create_user_tag(username)
    SnapshotTag.objects.get_or_create(snapshot_id=snapshot.pk, tag_id=tag.pk)
    return tag


def resolve_created_by_id(created_by_id: int | None = None, *, username: str | None = None) -> int:
    """The ``created_by_id`` a new crawl must carry, or a refusal.

    Used by the CLI paths (``archivebox add``, ``archivebox snapshot create``,
    ``archivebox crawl create``) that have no authenticated user. An unattributed
    crawl would produce untagged, invisible snapshots, so an add with no identity
    is a hard error instead of a silent one.
    """
    from django.contrib.auth import get_user_model

    if created_by_id:
        return int(created_by_id)

    requested = str(username or "").strip() or str(os.environ.get(ADD_USER_ENV) or "").strip()
    if requested:
        User = get_user_model()
        user = User.objects.filter(username=requested).first() or User.objects.filter(username__iexact=requested).first()
        if user is None:
            raise ValueError(f"no such user: {requested!r} (from --user or {ADD_USER_ENV})")
        if str(user.username).lower() == SYSTEM_USERNAME:
            raise ValueError(f"{SYSTEM_USERNAME!r} is a placeholder account, not an owner; name a real user")
        return user.pk

    raise ValueError(
        "refusing to create an unattributed crawl: pass --user <username> or set "
        f"{ADD_USER_ENV}, so its snapshots are tagged {USER_TAG_PREFIX}<username> and visible to that user. "
        "Without an owner they would be invisible to everyone.",
    )


def filter_assignable_tag_names(names: Iterable[str]) -> list[str]:
    """Drop reserved ``user-*`` names from a user-supplied tag list."""
    return [str(name).strip() for name in names if str(name).strip() and not is_user_tag(name)]


# ---------------------------------------------------------------------------
# Scoping reads
# ---------------------------------------------------------------------------


def can_see_everything(request: HttpRequest | None) -> bool:
    """Whether this request may use the deliberately-toggled cross-user view.

    A superuser *and* the session toggle. Being a superuser alone changes nothing.
    """
    user = getattr(request, "user", None)
    if not (user is not None and getattr(user, "is_authenticated", False) and getattr(user, "is_active", False)):
        return False
    if not getattr(user, "is_superuser", False):
        return False
    session = getattr(request, "session", None)
    return bool(session is not None and session.get(SEE_ALL_SESSION_KEY))


def set_see_everything(request: HttpRequest, enabled: bool) -> None:
    session = getattr(request, "session", None)
    if session is None:
        raise RuntimeError("sessions are not enabled; the see-all toggle needs a session")
    if enabled:
        session[SEE_ALL_SESSION_KEY] = True
    else:
        session.pop(SEE_ALL_SESSION_KEY, None)


def visible_usernames(request: HttpRequest | None) -> list[str]:
    """The ownership usernames this request may see. Empty means "nothing"."""
    if request is None:
        return []
    user = getattr(request, "user", None)
    if not (user is not None and getattr(user, "is_authenticated", False) and getattr(user, "is_active", False)):
        return []
    username = str(getattr(user, "username", "") or "").strip()
    return [username] if username else []


def scoped_snapshot_queryset(request: HttpRequest | None, queryset: Any) -> Any:
    """Narrow a Snapshot queryset to what ``request`` is allowed to see.

    ``request is None`` means "no request context" (local export / management
    command) and is left unscoped on purpose. An unauthenticated request sees
    nothing - an untagged snapshot is invisible to everyone, and a tagged one is
    invisible without a user.
    """
    if request is None:
        return queryset
    if can_see_everything(request):
        return queryset

    from archivebox.core.models import Tag

    usernames = visible_usernames(request)
    if not usernames:
        return queryset.none()

    tag_names = [user_tag_name(username) for username in usernames]
    owner_tag_ids = Tag.all_objects.filter(name__in=tag_names).values("id")
    return queryset.filter(tags__id__in=owner_tag_ids).distinct()


def owned_snapshot_queryset(username: str | None, queryset: Any) -> Any:
    """Narrow a Snapshot queryset to one user's captures, without a request.

    Used where the acting user comes from a model rather than a session: ONLY_NEW
    de-duplication ("have *I* already archived this URL?") and the API's reuse of an
    existing capture. Global de-duplication would let user A's capture of a URL
    silently satisfy user B's add, and B would end up with nothing.
    """
    from archivebox.core.models import Tag

    if not username:
        return queryset.none()
    owner_tag_ids = Tag.all_objects.filter(name=user_tag_name(username)).values("id")
    return queryset.filter(tags__id__in=owner_tag_ids).distinct()


def crawl_owner_username(crawl: Any) -> str | None:
    """The username a crawl's snapshots belong to (created_by, then the add env)."""
    created_by = getattr(crawl, "created_by", None)
    username = str(getattr(created_by, "username", "") or "").strip()
    if username and username.lower() != SYSTEM_USERNAME:
        return username
    try:
        return add_user_from_env()
    except ValueError:
        return None


def scoped_crawl_queryset(request: HttpRequest | None, queryset: Any) -> Any:
    """Narrow a Crawl queryset to the request user's own crawls."""
    if request is None:
        return queryset
    if can_see_everything(request):
        return queryset
    usernames = visible_usernames(request)
    if not usernames:
        return queryset.none()
    return queryset.filter(created_by__username__in=usernames)


def scoped_archiveresult_queryset(request: HttpRequest | None, queryset: Any) -> Any:
    """Narrow an ArchiveResult queryset by the ownership of its snapshot."""
    if request is None:
        return queryset
    if can_see_everything(request):
        return queryset

    from archivebox.core.models import Tag

    usernames = visible_usernames(request)
    if not usernames:
        return queryset.none()
    owner_tag_ids = Tag.all_objects.filter(name__in=[user_tag_name(username) for username in usernames]).values("id")
    return queryset.filter(snapshot__tags__id__in=owner_tag_ids).distinct()


def can_access_snapshot(request: HttpRequest | None, snapshot: Any) -> bool:
    """The single-object form of :func:`scoped_snapshot_queryset`."""
    if snapshot is None:
        return False
    if request is None:
        return True
    if can_see_everything(request):
        return True
    usernames = visible_usernames(request)
    if not usernames:
        return False
    owners = snapshot_owner_names(snapshot)
    return any(owner in usernames for owner in owners)


def require_snapshot_access(request: HttpRequest, snapshot: Any, *, status: int = 404) -> Any:
    """Raise ``Http404`` unless ``request`` may see ``snapshot``.

    Used on every URL that names a snapshot directly, so a snapshot that is not
    yours answers 404 - the same answer as one that does not exist - instead of
    being hidden from a list while still readable by URL.
    """
    if not can_access_snapshot(request, snapshot):
        raise Http404("Snapshot not found")
    return snapshot


def scope_summary(request: HttpRequest | None) -> dict[str, Any]:
    """Template-facing description of the active scope (see ``archivebox_globals``)."""
    usernames = visible_usernames(request)
    sees_all = can_see_everything(request)
    user = getattr(request, "user", None)
    return {
        "username": usernames[0] if usernames else "",
        "tag": user_tag_name(usernames[0]) if usernames else "",
        "see_all": sees_all,
        "can_see_all": bool(user is not None and getattr(user, "is_superuser", False) and getattr(user, "is_authenticated", False)),
        "enabled": bool(usernames),
    }
