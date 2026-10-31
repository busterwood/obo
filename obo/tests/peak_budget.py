"""Authoritative compiler live-peak budget for this numeric branch.

The user explicitly approved 2,500,000 *decimal bytes* for numeric work.  This
is a numeric-branch decision, not a change to the historical lambda release
budget (which remains 2,000,000 in its own release record).

The empty-array fault is a *savings regression*: the normal runtime shares
zero-capacity array blocks, while the zero-allocation mutant allocates a heap
block for every empty array.  The pre-float historical delta was 44,672 bytes
(baseline 1,963,664 -> mutant 2,008,336); after the exact float converter the
fresh numeric-branch delta is 45,600 bytes (latest same-source run baseline
2,027,664 -> mutant 2,073,264).  The absolute cap alone cannot catch it once the baseline is above
2,000,000, so mutate_io additionally requires the mutant's self-compile peak to
exceed the same-source baseline by at least EMPTY_ARRAY_MIN_DELTA.  Path/temp-dir
variance is only hundreds of bytes; 30,000 bytes is a deliberately conservative
floor below both measured deltas and above that noise.
"""

PEAK_BUDGET = 2_500_000

# Fresh exact-converter measurement is 45,600 bytes (latest run baseline
# 2,027,664 -> mutant 2,073,264); the old 44,672 was the pre-float historical
# figure.  These
# numbers are illustrative, not a fixed guarantee.  30,000 is the documented
# contract: above path noise and below the shared-empty-block saving.
EMPTY_ARRAY_MIN_DELTA = 30_000


def empty_array_regression(base_peak: int, mutant_peak: int) -> tuple[bool, int]:
    """Return (caught, delta) for the self-compile savings oracle."""
    delta = mutant_peak - base_peak
    return delta >= EMPTY_ARRAY_MIN_DELTA, delta
