# Releasing

A release is the `make dist` tree that CI built for a commit on `main`, published only
after that very tree was tested on the board. A passing STA doesn't prove a bitstream
works (README, "Clock"), so the board test is the gate, and the annotated tag carries
its evidence: `release.yml` publishes only if the tag's `pack.fs sha256` matches the CI
artifact of the tagged commit. Nothing is rebuilt for the release, so it doesn't matter
whether a rebuild would reproduce the same bits.

Versions are tags `vMAJOR.MINOR.PATCH`. They are independent of the first stage's own
`STAGE1_VERSION` (bump that when the first stage changes; the second stage gets it, and
`MANIFEST.json` records it) and of the VUX9 header version (the file format).
A patch release keeps applications and SD cards working; a release that breaks them,
including one for a later hardware revision, raises the minor or major version and says
so in its tag message (docs/APP_DEVELOPMENT.md, "Hardware revisions and compatibility").

## 1. Get the release candidate

Every push to `main` uploads `vux9k-dist-<sha>` (kept 90 days) from the CI run:

```sh
SHA=$(git rev-parse origin/main)
RUN=$(gh run list --workflow ci.yml --commit $SHA --event push --status success --json databaseId --jq '.[0].databaseId')
gh run download $RUN --name vux9k-dist-$SHA --dir build/candidate
tar -xzf build/candidate/vux9k-dist.tar.gz -C build/candidate    # -> build/candidate/dist
```

## 2. Test it on the board

```sh
make test-hw-dist DIST=build/candidate/dist
git switch --detach $SHA && make timing hw-smoke
```

`test-hw-dist` loads the candidate's `pack.fs` and runs the hardware suite with its
second stage and demos (14 tests; it loads `pack.fs` again after each I-RAM demo); nothing
is rebuilt (the card stays in the board and must be FAT32). `make test-hw-irq-echo` runs
the interrupt-driven UART echo through the second stage (it builds the demos locally). `hw-smoke` runs `hw_test` on every seed of a
local place and route of the same commit, which covers the CPU more thoroughly than the
suite. The candidate's seed is one of them (`MANIFEST.json`); if the local `pack.fs` has
the same SHA-256 as the candidate's, hw-smoke tested exactly its placement (note it in
the tag).

The way up from the previous release was tested with its slots (`make test-hw-upgrade`)
until the two-stage boot replaced them, which v0.2.x cards can't follow. The move from
v0.2.x to the two-stage boot gets its own board test before the release that brings it;
until then, describe the steps (new bitstream, FAT32 card with `VUX9K\STAGE2.BIN`) in the
release notes.

## 3. Tag

```sh
python3 scripts/release_check.py template build/candidate/dist --version vX.Y.Z > build/tag-message.txt
$EDITOR build/tag-message.txt      # fill in the results, compatibility and the release notes
python3 scripts/release_check.py verify build/candidate/dist build/tag-message.txt --commit $SHA
git tag -a vX.Y.Z $SHA -F build/tag-message.txt --cleanup=verbatim
git push origin vX.Y.Z
```

The message is three parts separated by blank lines: the version, the fields (one
`key: value` per line), and the release notes in Markdown (`--cleanup=verbatim` keeps
their `#` headings, which git would otherwise strip as comments). Fill in every `<fill in>`:
- `test-hw` and `hw-smoke`: the board results of step 2.
- `compatibility`: whether the previous release's applications and SD cards keep working;
  if they don't, why the minor or major version went up.
- The release notes: what changed since the previous release, how to upgrade, and any
  host setup a user must redo.

`release.yml` then checks that the tag is annotated and on `main`, downloads the CI
artifact of the tagged commit, runs `release_check.py verify` (SHA256SUMS, a clean build
of that commit, `pack.fs` equal to the tested one, results filled in) and publishes:
the archive `vux9k-<tag>-linux-x86_64.tar.gz`, the bitstream and the Zephyr BSP on their
own, and `SHA256SUMS`, with release notes from `release_check.py notes`: the fields in a
code block, then the tag's release notes.

## Trying the workflow without publishing

Tag with a name that doesn't start with `v` (pushing it doesn't start `release.yml`) and
start a dry run, which does everything but publish and keeps the assets as a workflow
artifact:

```sh
git tag -a dryrun-1 $SHA -F build/tag-message.txt --cleanup=verbatim && git push origin dryrun-1
gh workflow run release.yml -f tag=dryrun-1 -f dry_run=true
```

Delete the tag afterwards (`git push origin :dryrun-1 && git tag -d dryrun-1`).

## When it fails

- **No artifact** (the CI run is older than 90 days, or it failed): re-run CI for the
  commit, then test the new artifact (step 2) and tag again. A rebuild may or may not
  produce the same `pack.fs`; only a tested one is published.
- **`pack.fs` mismatch:** the tag names a bitstream other than CI's for that commit, e.g.
  the board test used a local build. Test the CI artifact itself.
- To retry a tag, delete it (`git push origin :vX.Y.Z`, `git tag -d vX.Y.Z`) and push it
  again, or run `release.yml` by hand with `dry_run` off.
