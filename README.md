# ArchiveBox — homelab fork

This is the emrys.dev homelab's fork of [ArchiveBox](https://github.com/ArchiveBox/ArchiveBox),
the self-hosted web archiver. It adds per-user isolation: every snapshot belongs to a user, and
every list, search, download and API read is filtered by that owner on the server.

**Upstream project: https://github.com/ArchiveBox/ArchiveBox**

Upstream documents installation, the archive layout and the CLI; this README covers only the fork.
The unedited upstream README is preserved at
[README.upstream.md](https://github.com/Emrys-Homelab/ArchiveBox/blob/main/README.upstream.md).

## What this fork changes

- **0001 — every snapshot has an owner.** A `user-<username>` tag written once at creation, and a
  `Tag` default manager that hides `user-*` names from every UI and API surface at once.
- **0002 — access is enforced server-side.** Ownership is checked before permissions; a foreign
  snapshot answers 404, not a login redirect. The only cross-user view is a POST-only superuser
  toggle, announced by a banner while it is on.
- **0003 — admin, search and progress are scoped.** Snapshot, archive-result, crawl and schedule
  querysets, custom admin views, search, progress counters and per-owner `ONLY_NEW` filter by
  owner.
- **0004 — the API follows the token's owner.** Every `/api/v1/core/*` list, detail, RSS, crawl,
  tag-export and `/cli/*` endpoint is scoped to the API token's user.
- **0005 — adds must name an owner.** `archivebox add --user` / `ARCHIVEBOX_ADD_USER`; an add with
  no resolvable owner is refused rather than creating an invisible capture.
- **0006 — the existing archive goes to `myrddin`.** Migration `core.0061` tags every pre-fork
  snapshot `user-myrddin`, and raises rather than finishing with an untagged row.
- **0007 — the isolation is documented.** `docs/user-isolation.md`: the model, every enforcement
  point, and the known limits.

## Branches

| Branch | What it is |
| --- | --- |
| `main` | upstream tag `v0.9.77` plus one documentation commit: this README, `README.upstream.md`, `FORK.md` and `patches/`. No code changes. |
| `homelab` | the fork: upstream `v0.9.77` plus the seven patches, and the same README. **This is the default branch.** |

Base: upstream tag `v0.9.77`, commit `289d6a5904642b02c618e77f225aa28bd201a145`.

## Build and deploy

The image is an **overlay** on the stock v0.9.77 image: a patch that replaces a set of Python
modules, two templates and one migration does not justify rebuilding Chromium and 77 plugins. The
context is generated from `patches/` every time; no generated file is committed.

```sh
~/backup/gitops/apps/archivebox/image/make-context.sh
~/homelab/scripts/build-image.sh ~/work/archivebox-context archivebox:0.9.77-homelab.5
```

The deployment pins the digest:

```
registry.emrys.dev/homelab/archivebox:0.9.77-homelab.5@sha256:d9f8b42041a76c283c25dc524b990661c53abc9e68b2e86d5b9fb52ab4480fc8
```

It lives in `~/backup/gitops/apps/archivebox/deployment.yaml`, on both the init container and the
app container. BuildKit can report a failure after the image manifest has been pushed (the
buildcache upload has twice returned 404), so confirm the digest at the registry rather than
trusting the exit code.

## Patches

The series is in [`patches/`](https://github.com/Emrys-Homelab/ArchiveBox/tree/main/patches), one
file per commit. Applying it to `main` reproduces the code on `homelab` (`main` also carries the
`FORK.md` and `patches/` this step uses):

```sh
git clone https://github.com/Emrys-Homelab/ArchiveBox.git
cd ArchiveBox
git checkout main
git am patches/*.patch
```

To rebase onto a newer upstream release:

```sh
git fetch https://github.com/ArchiveBox/ArchiveBox 'refs/tags/v*:refs/tags/v*'
git checkout -B rebase v0.9.78
git am -3 patches/*.patch          # resolve conflicts here
git format-patch v0.9.78..rebase -o patches/
```

Then point `homelab` at the rebased branch, rebuild `main` as the new tag plus a documentation
commit carrying `README.md`, `README.upstream.md`, `FORK.md` and the regenerated `patches/`,
rebuild the image, repin the digest, and deploy. After the rebase, re-read every
`Snapshot.objects` read path: a new read path upstream is a new leak.

## Caveats

- **Isolation changes what a user can see.** Every snapshot is owned, and a snapshot with no
  owner is invisible to everyone. Before deploying, confirm that existing snapshots are covered by
  the `core.0061` backfill (`myrddin` here, on the operator's rule).
- Superusers are not exempt by default, but can switch on a POST-only "see all" session view;
  while it is on, every admin page shows an amber banner with the button that turns it off.
- Isolation is enforced in the web UI and API, not on the filesystem: anyone with a shell in the
  container or on the volume can read the archive.
- The image is an overlay pinned to the 0.9.77 digest: a new upstream release means regenerating
  the context, rebuilding and repinning.
- Upstream's install instructions describe the stock image and do not apply to this build.

## Licence

Unchanged from upstream: **MIT**
([LICENSE](https://github.com/Emrys-Homelab/ArchiveBox/blob/main/LICENSE)).
