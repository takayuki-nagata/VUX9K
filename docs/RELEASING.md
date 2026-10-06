# Releasing

A release is the `make dist` tree that CI built for a commit on `main`, published only
after that very tree was tested on the board. A passing STA doesn't prove a bitstream
works (README, "Clock"), so the board test is the gate, and the annotated tag carries
its evidence: `release.yml` publishes only if the tag's `pack.fs sha256` matches the CI
artifact of the tagged commit. Nothing is rebuilt for the release, so it doesn't matter
whether a rebuild would reproduce the same bits.

Versions are tags `vMAJOR.MINOR.PATCH`. They are independent of the Boot Manager's own
`BOOT_MGR_VERSION` (bump that when the Boot Manager changes, so boards install it from
slot 0) and of the VUX9 header version (the slot format).
A patch release keeps applications and slots working; a release that breaks them,
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
gh release download <previous tag> -p 'vux9k-*-linux-x86_64.tar.gz' -D build/previous
tar -xzf build/previous/vux9k-<previous tag>-linux-x86_64.tar.gz -C build/previous
make test-hw-upgrade OLD=build/previous/vux9k-<previous tag> NEW=build/candidate/dist
git switch --detach $SHA && make timing hw-smoke
```

`test-hw-dist` loads the candidate's `pack.fs` and runs the hardware suite with its
Boot Manager and demos; nothing is rebuilt. `hw-smoke` runs `hw_test` on every seed of a
local place and route of the same commit, which covers the CPU more thoroughly than the
suite. The candidate's seed is one of them (`MANIFEST.json`); if the local `pack.fs` has
the same SHA-256 as the candidate's, hw-smoke tested exactly its placement (note it in
the tag).

`test-hw-upgrade` (`scripts/test_hw_upgrade.py`) tests the way up from the previous
release: its slots, written with its own `vux_tool.py`, must boot under the candidate's
bitstream; its tool must work with the candidate's Boot Manager and the candidate's tool
with its Boot Manager; and its bitstream must install the candidate's Boot Manager from
slot 0 (skipped when the Boot Manager version is unchanged). It overwrites SD slots 0-4
and leaves the candidate's bitstream in SRAM. Add its result to the tag message next to
test-hw and hw-smoke (e.g. `test-hw-upgrade: from v0.1.0, 28 PASS 0 FAIL`).

## 3. Tag

```sh
python3 scripts/release_check.py template build/candidate/dist --version v0.1.0 > build/tag-message.txt
$EDITOR build/tag-message.txt      # fill in test-hw and hw-smoke, add test-hw-upgrade
git tag -a v0.1.0 $SHA -F build/tag-message.txt --cleanup=verbatim
git push origin v0.1.0
```

`release.yml` then checks that the tag is annotated and on `main`, downloads the CI
artifact of the tagged commit, runs `release_check.py verify` (SHA256SUMS, a clean build
of that commit, `pack.fs` equal to the tested one, results filled in) and publishes:
the archive `vux9k-<tag>-linux-x86_64.tar.gz`, the bitstream and the Zephyr BSP on their
own, and `SHA256SUMS`, with the tag message as release notes.

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
- To retry a tag, delete it (`git push origin :v0.1.0`, `git tag -d v0.1.0`) and push it
  again, or run `release.yml` by hand with `dry_run` off.
