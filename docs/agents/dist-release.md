---
paths:
  - "scripts/make_dist.py"
  - "scripts/check_dist.py"
  - "scripts/release_check.py"
  - ".github/**"
  - "docs/*.md"
---

# Distribution and releases

Read before changing `make dist`, the release scripts or workflows, or the application docs. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## Distribution and releases: `make dist`, tags vouch for a board test

`make dist` (`scripts/make_dist.py`) assembles `build/dist/`, what an application
developer gets (docs/APP_DEVELOPMENT.md is its guide); `make check-dist` copies it away
from the repo and runs the demos with the shipped emulator and Python module, then
`release_check.py selftest`. CI runs both after `make sta` and, on pushes, uploads the tree
as `vux9k-dist-<sha>`. `release.yml` publishes that artifact for an annotated `v*` tag only
if the tag message's `pack.fs sha256` matches it (docs/RELEASING.md): tag the commit whose
CI artifact was tested on the board, never a local build. (The way up from the previous
release was tested with its slots until the two-stage boot replaced them; the move from
v0.2 to the two-stage boot gets its own test before that release.) The tag message's
fields (`release_check.py`'s `FIELDS`, all required) and the release notes built from it
(`release_check.py notes`) live in that script, not in the workflow. Keep in sync:
- The dist's contents live in `make_dist.py`'s `FILES`, `check_dist.py`'s `REQUIRED` and
  docs/APP_DEVELOPMENT.md; the docs' Python example is `check_dist.PY_EXAMPLE`, verbatim.
- MANIFEST's tool pins are parsed from `ci.yml`; `make_dist.py` fails if a pattern stops
  matching, so update `PINS` when ci.yml changes shape.
- The distribution is for applications: the second stage for the card
  (`sd/VUX9K/STAGE2.BIN`; the first stage is in the bitstream), no firmware hex for the
  emulator (applications start there with `--no-firmware --load IMAGE --mode riscv|hack`),
  no SD images.
- What applications may rely on is docs/APP_DEVELOPMENT.md, "Hardware revisions and
  compatibility": a patch release keeps applications and SD cards working; a release that
  breaks them raises minor/major and says so in its tag message's required
  `compatibility` field.
