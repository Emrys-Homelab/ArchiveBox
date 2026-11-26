"""Attribute every snapshot that already existed to ``user-myrddin``.

THE PROBLEM THIS SOLVES. Isolation reads ``user-<name>`` ownership tags, and an
untagged snapshot is invisible to everyone (that is the fail-closed rule). The
archive restored into this deployment predates the fork, so every snapshot in it
carries no ownership tag and would disappear from every view on the first boot of
the fork.

THE OPERATOR'S RULE. "Every item currently in the archive is owned by myrddin."
He was the only user of this collection when the fork was written, and the two
other accounts that exist (``system``, the placeholder owner upstream assigns to
work nobody claimed, and the ``verifier`` account created for the isolation test)
own nothing. So one explicit, recorded decision - not a heuristic - attributes the
whole existing collection to him.

WHY A MIGRATION. It runs exactly once, inside ``archivebox server --init``, before
the server accepts a request, and it is recorded in ``django_migrations`` like any
other schema change. A one-off ``kubectl exec`` command would be an undocumented
step that a rebuilt cluster would forget; this cannot be forgotten.

REVERSIBILITY. There is none, deliberately. Un-applying would strip the ownership
tags and make the archive invisible again; a rollback that erases access is worse
than a rollback that leaves tags behind. Rolling back the image leaves the tags in
place and the older code simply does not read them.
"""

from django.db import migrations

#: The operator's decision: the pre-fork archive belongs to this account.
BACKFILL_USERNAME = "myrddin"

USER_TAG_PREFIX = "user-"


def backfill_existing_snapshots(apps, schema_editor):
    Tag = apps.get_model("core", "Tag")
    Snapshot = apps.get_model("core", "Snapshot")
    SnapshotTag = apps.get_model("core", "SnapshotTag")
    db = schema_editor.connection.alias

    tag_name = f"{USER_TAG_PREFIX}{BACKFILL_USERNAME}"
    snapshot_ids = list(Snapshot.objects.using(db).values_list("id", flat=True))
    if not snapshot_ids:
        print(f"user_scope backfill: the collection is empty; no {tag_name} tag created")
        return

    # Historical models do not carry the default manager's ownership-tag filter
    # (managers are not serialised into migration state), so these lookups see
    # ``user-*`` rows.
    tag, created = Tag.objects.using(db).get_or_create(name=tag_name)
    already = set(SnapshotTag.objects.using(db).filter(tag_id=tag.pk, snapshot_id__in=snapshot_ids).values_list("snapshot_id", flat=True))
    missing = [snapshot_id for snapshot_id in snapshot_ids if snapshot_id not in already]
    SnapshotTag.objects.using(db).bulk_create(
        [SnapshotTag(snapshot_id=snapshot_id, tag_id=tag.pk) for snapshot_id in missing],
        ignore_conflicts=True,
    )

    owned = set(
        SnapshotTag.objects.using(db)
        .filter(snapshot_id__in=snapshot_ids, tag__name__istartswith=USER_TAG_PREFIX)
        .values_list("snapshot_id", flat=True),
    )
    untagged = [snapshot_id for snapshot_id in snapshot_ids if snapshot_id not in owned]

    print(
        f"user_scope backfill: {len(snapshot_ids)} snapshot(s) in the collection; "
        f"{len(missing)} tagged as {tag_name} by this migration, {len(already)} already carried it, "
        f"{len(untagged)} left untagged (tag {'created' if created else 'reused'})",
    )
    if untagged:
        raise RuntimeError(
            f"user_scope backfill left {len(untagged)} snapshot(s) without an ownership tag; "
            "they would be invisible to every user. Refusing to finish the migration.",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0060_snapshot_status_modified_idx"),
    ]

    operations = [
        migrations.RunPython(backfill_existing_snapshots, migrations.RunPython.noop),
    ]
