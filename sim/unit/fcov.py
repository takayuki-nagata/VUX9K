# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Functional coverage of the unit tests (cocotb-coverage): which cases of the specification
the boundary and random stimulus actually reached, as opposed to which RTL lines ran
(`make coverage`).

Each test module defines its cover points with `point()`/`cross()` on a plain sampling
function, calls it from the function that applies one stimulus and checks the model, and
calls `export()` at the end of every test. The database lives in the simulator process
and accumulates over the module's tests; export() writes it to fcov.yml in the run
directory (the cwd, see sim_runner). scripts/fcov_report.py merges the files and checks
coverage/thresholds.toml's [fcov] table (make coverage-fcov).

Names are "<group>.<point>"; the threshold applies per group (the test module's area).
Sampling functions take positional arguments only (a cocotb-coverage rule).
"""

from cocotb_coverage.coverage import CoverCross, CoverPoint, coverage_db

# classify32() names, for bins
CLASSES32 = ("zero", "one", "minus1", "min_neg", "max_pos", "pos", "neg")


def classify32(x):
    """The class of a 32-bit value where arithmetic changes behavior."""
    x &= 0xFFFF_FFFF
    if x == 0:
        return "zero"
    if x == 1:
        return "one"
    if x == 0xFFFF_FFFF:
        return "minus1"
    if x == 0x8000_0000:
        return "min_neg"
    if x == 0x7FFF_FFFF:
        return "max_pos"
    return "neg" if x & 0x8000_0000 else "pos"


def point(name, bins, xf=None):
    """A cover point over the sampling function's arguments (a tuple if several, or xf's result)."""
    return CoverPoint(name, xf=xf, bins=list(bins))


def cross(name, items, ign_bins=()):
    return CoverCross(name, items=list(items), ign_bins=list(ign_bins))


def export(path="fcov.yml"):
    coverage_db.export_to_yaml(path)
