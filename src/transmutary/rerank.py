"""L2 semantic grouping — the funnel's middle layer (U-rerank; KTD-A/E/G/H).

The CONTEXT funnel is ``L1 rule → L2 semantic group → L3 LLM-as-judge``. This
module is L2: given the L1 survivors and an ``embed_fn``, it folds approximately
duplicate texts into GROUPS so the expensive L3 layer runs once per group instead
of once per item — WITHOUT dropping any real signal (zero-miss).

Hard invariants (each a zero-miss safety property):

* **representative-linkage (KTD-A, P0).** A new text is compared ONLY to each
  existing group's representative; it joins a group iff its cosine similarity is
  STRICTLY greater than the threshold, otherwise it starts a new group. We do NOT
  use single-linkage (compare against any group member): single-linkage is
  transitive, so with cos(A,B)>τ and cos(B,C)>τ but cos(A,C)≤τ it would wrongly
  merge A and C — two genuinely different signals — and the judge would then see
  them as one. Representative-linkage cannot chain distinct signals together.

* **member mapping (KTD-A, P0).** Each :class:`Group` carries ``member_indices``
  (the representative included), so the caller can back-fill a per-group verdict
  onto EVERY member — evidence is never lost to "we only judged the representative".

* **zero/empty vectors are isolated (P2).** A zero (or empty) vector has no
  direction; cosine is undefined (0/0 → NaN). Rather than risk a NaN comparison
  silently merging unrelated items, such a vector ALWAYS forms its own group and is
  never used as a merge target.

Cosine is computed by hand (no numpy) to avoid a heavy dependency. The threshold
is a module constant, strict ``>`` (boundary jitter favors NOT merging — the
zero-miss direction), uncalibrated-conservative per KTD-E.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Cosine threshold for two texts to land in the same group. Uncalibrated default
# is deliberately STRICT (KTD-E "宁少合并勿误并"): before this is tuned against a
# real embedding model's similarity distribution, a high bar means we under-merge
# (spend a little extra L3) rather than over-merge (risk masking a real signal).
#
# CALIBRATION DEFERRED (KTD-E sub-step / KTD-F): the spec's calibration sub-step —
# measuring this model's cosine distribution over known should-merge / should-split
# fault-text pairs (outage/crash/security buckets) and recording the worked basis
# here — is intentionally deferred until a live embedding provider is wired in
# (KTD-F gates it; MVP runs entirely on mocked deterministic vectors). The deferral
# is SAFE because the strict default only risks under-merging, never over-merging,
# so the only consequence is a few extra L3 calls — never a masked real signal. See
# the plan's "Deferred to Follow-Up Work" list.
L2_GROUP_THRESHOLD = 0.90

# Embedding cost ceiling (KTD-H). If a single batch exceeds this many items, L2 is
# SKIPPED entirely and every item passes through to L3 (the zero-miss degrade,
# same posture as KTD-B). This bounds the embedding cost of an issue surge.
L2_MAX_EMBED_ITEMS = 200


@dataclass
class Group:
    """One semantic group: a representative plus all its members.

    ``representative_index`` and ``member_indices`` index into the ORIGINAL input
    list passed to :func:`group_semantic`. ``member_indices`` always includes
    ``representative_index`` and preserves input order.
    """

    representative_index: int
    member_indices: list[int]


def _cosine(a: list[float], b: list[float]) -> float:
    """Hand-rolled cosine similarity. Returns 0.0 if either vector has zero norm.

    Returning 0.0 (not NaN) on a zero-norm vector keeps the comparison total and
    safe; callers additionally isolate zero vectors up front so this guard is a
    belt-and-braces against division by zero.
    """
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _is_zero(vec: list[float]) -> bool:
    """True for an empty or all-zero vector (no direction → must self-isolate)."""
    return not any(v != 0.0 for v in vec)


def group_semantic(
    texts: list[str],
    *,
    embed_fn,
    threshold: float = L2_GROUP_THRESHOLD,
) -> list[Group]:
    """Fold ``texts`` into semantic groups via representative-linkage (KTD-A).

    Args:
        texts: the strings to group (e.g. L1-survivor issue texts).
        embed_fn: ``Callable[[list[str]], list[list[float]]]`` returning one vector
            per text. Called exactly once for the whole batch. An empty ``texts``
            returns ``[]`` WITHOUT calling ``embed_fn``.
        threshold: minimum cosine (strict ``>``) for a text to join a group.

    Returns:
        Groups in order of first appearance. Each group's representative is its
        first (lowest-index) member; ``member_indices`` includes the representative.

    The caller is responsible for back-filling any per-group verdict onto every
    ``member_indices`` entry (zero-miss). ``embed_fn`` errors propagate to the
    caller, which is expected to degrade to full L3 (zero-miss).
    """
    if not texts:
        return []

    vectors = embed_fn(texts)

    # (representative_index, representative_vector) for each open group, in order.
    reps: list[tuple[int, list[float]]] = []
    members: dict[int, list[int]] = {}
    order: list[int] = []

    for i, vec in enumerate(vectors):
        # Zero/empty vectors never merge and never act as a merge target (P2).
        if _is_zero(vec):
            reps.append((i, vec))
            members[i] = [i]
            order.append(i)
            continue

        best_rep: int | None = None
        best_sim = threshold  # strict ">": must BEAT the threshold to join
        for rep_idx, rep_vec in reps:
            if _is_zero(rep_vec):
                continue
            sim = _cosine(vec, rep_vec)
            if sim > best_sim:
                best_sim = sim
                best_rep = rep_idx

        if best_rep is None:
            reps.append((i, vec))
            members[i] = [i]
            order.append(i)
        else:
            members[best_rep].append(i)

    return [Group(representative_index=r, member_indices=members[r]) for r in order]
