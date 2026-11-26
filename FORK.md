# This is the `homelab` fork of ArchiveBox

| | |
| --- | --- |
| Upstream | <https://github.com/ArchiveBox/ArchiveBox> (MIT) |
| Base | tag **v0.9.77**, commit `289d6a590` |
| Fork branch | `homelab` - seven commits, one per change |
| Series of record | `MyrddinE/homelab`, `patches/archivebox/` (long-form notes, build, rebase procedure) |
| Deployed image | `registry.emrys.dev/homelab/archivebox:0.9.77-homelab.3@sha256:d423c085847c98ad6b8933cd851d5e1254003552823fa20f3231f9e4f7253c81` |
| Pinned in | `MyrddinE/backup`, `gitops/apps/archivebox/deployment.yaml` (both containers) |

## What the fork adds

Per-user isolation. Every snapshot carries a `user-<username>` tag written once at
creation - the web add form's session user, the API token's owner, the parent
snapshot's owner for discovered links, or `ARCHIVEBOX_ADD_USER` for a pod add - and
every read path is filtered by it on the server: the snapshot changelist and its
search, the change form (a foreign id is 404), the archive-result and crawl admins,
the tag index and its counts, the public index, every `/api/v1/core/*` list and
detail endpoint, the `/cli/*` commands, progress counters, and every direct snapshot
URL. A snapshot that is not yours answers 404, not a login redirect.

`user-*` tags are invisible: the `Tag` default manager excludes them, so they never
appear in a tag index, picker, autocomplete, card, export or API response.
`Tag.all_objects` is the unfiltered manager, used only by `core/user_scope.py` and
the backfill migration.

Superusers are not exempt. The only way to read another user's captures is
`POST /user-scope/see-all/`, which requires an active superuser, is POST-only, lives
in the session, and is announced by the admin banner. An untagged snapshot is
invisible to everyone, which is why the CLI refuses an add with no resolvable owner.

Migration `core.0061_user_scope_backfill` attributes every pre-existing snapshot to
`myrddin` and raises rather than finishing if anything would be left untagged.

## Build

The image is an **overlay** on the upstream digest (a thirteen-module patch does not
justify rebuilding Chromium and 77 plugins):

```sh
~/backup/gitops/apps/archivebox/image/make-context.sh   # extract v0.9.77, git am patches/, stage the overlay
~/homelab/scripts/build-image.sh ~/work/archivebox-context archivebox:0.9.77-homelab.N
# then pin the digest the registry reports, in gitops/apps/archivebox/deployment.yaml
```

`main` on this repository is upstream v0.9.77 plus one commit carrying this file and
`patches/`; `homelab` is the fork. `git am patches/*.patch` on a clone of v0.9.77
reproduces `homelab`.

## Verify

`MyrddinE/homelab`, `patches/archivebox/tests/`: `test-isolation.py` (41 assertions
against a scratch collection inside the image, the pre-deploy gate) and
`live-verify.py` (45 assertions against the live deployment with two real users).
Both pass.
