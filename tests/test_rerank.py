"""L2 group_semantic tests — representative-linkage, zero-vector isolation, member
mapping. All embed_fn mocks are deterministic 2-D unit vectors by angle (cos θ is
the cosine similarity); no real network.
"""

from __future__ import annotations

import math

from transmutary.rerank import (
    L2_GROUP_THRESHOLD,
    Group,
    _cosine,
    _is_zero,
    group_semantic,
)


def _unit(deg: float) -> list[float]:
    """A 2-D unit vector at ``deg`` degrees from the x-axis. v0 = _unit(0) = [1,0].

    cos(angle_between(_unit(a), _unit(b))) == cos(|a-b| degrees), so angle gaps map
    directly to cosine similarity: 0°→1.0, 20°→0.94, 40°→0.766, 90°→0.0.
    """
    r = math.radians(deg)
    return [math.cos(r), math.sin(r)]


def _fixed(vectors: list[list[float]]):
    """An embed_fn returning the given vectors in order (asserts arity)."""

    def fn(texts: list[str]) -> list[list[float]]:
        assert len(texts) == len(vectors)
        return [list(v) for v in vectors]

    return fn


# --- cosine primitive --------------------------------------------------------
def test_cosine_parallel_and_orthogonal():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert abs(_cosine(_unit(0), _unit(90))) < 1e-9


def test_cosine_zero_vector_returns_zero_not_nan():
    val = _cosine([0.0, 0.0], [1.0, 0.0])
    assert val == 0.0
    assert not math.isnan(val)


def test_is_zero():
    assert _is_zero([])
    assert _is_zero([0.0, 0.0])
    assert not _is_zero([0.0, 0.1])


# --- empty / trivial ---------------------------------------------------------
def test_empty_input_does_not_call_embed_fn():
    calls = {"n": 0}

    def fn(texts):
        calls["n"] += 1
        return []

    assert group_semantic([], embed_fn=fn) == []
    assert calls["n"] == 0


def test_single_text_one_group():
    groups = group_semantic(["a"], embed_fn=_fixed([_unit(0)]))
    assert groups == [Group(representative_index=0, member_indices=[0])]


# --- grouping behavior -------------------------------------------------------
def test_identical_vectors_one_group_with_member_mapping():
    groups = group_semantic(["a", "b", "c"], embed_fn=_fixed([_unit(0), _unit(0), _unit(0)]))
    assert len(groups) == 1
    assert groups[0].representative_index == 0
    assert sorted(groups[0].member_indices) == [0, 1, 2]


def test_orthogonal_vectors_separate_groups():
    groups = group_semantic(["a", "b"], embed_fn=_fixed([_unit(0), _unit(90)]))
    assert len(groups) == 2
    assert [g.member_indices for g in groups] == [[0], [1]]


def test_chained_single_linkage_counterexample():
    # 0°, 20°, 40°: cos(0,20)=cos(20,40)=0.94 > 0.90, but cos(0,40)=0.766 < 0.90.
    # representative-linkage compares ONLY to representatives:
    #   - 20° joins rep 0° (0.94 > 0.90)
    #   - 40° vs rep 0° (0.766) does NOT clear the bar → its OWN group.
    # single-linkage would have chained 0°-20°-40° into one group, wrongly merging
    # the genuinely-different 0° and 40° signals. This test is the guard.
    groups = group_semantic(
        ["a", "b", "c"], embed_fn=_fixed([_unit(0), _unit(20), _unit(40)])
    )
    assert len(groups) == 2
    assert groups[0].representative_index == 0
    assert sorted(groups[0].member_indices) == [0, 1]  # 0° + 20°
    assert groups[1].representative_index == 2
    assert groups[1].member_indices == [2]  # 40° NOT transitively merged


def test_distinct_signals_below_threshold_never_merged():
    # 50° apart → cos = 0.64 < 0.90 → must stay separate (no false merge).
    groups = group_semantic(["a", "b"], embed_fn=_fixed([_unit(0), _unit(50)]))
    assert len(groups) == 2


def test_strict_greater_than_boundary_does_not_merge():
    # Exactly at the threshold must NOT merge (strict >, boundary favors no-merge).
    # Build two vectors whose cosine == L2_GROUP_THRESHOLD precisely.
    theta = math.degrees(math.acos(L2_GROUP_THRESHOLD))
    groups = group_semantic(["a", "b"], embed_fn=_fixed([_unit(0), _unit(theta)]))
    assert len(groups) == 2  # cos == threshold, not > threshold → separate


def test_zero_vector_isolated_and_does_not_merge_others():
    # Index 0 and 2 are identical (0°) and must merge; index 1 is a zero vector and
    # must form its own group, never absorbed by or absorbing a non-zero vector.
    groups = group_semantic(
        ["a", "z", "b"], embed_fn=_fixed([_unit(0), [0.0, 0.0], _unit(0)])
    )
    member_sets = sorted(sorted(g.member_indices) for g in groups)
    assert [1] in member_sets  # zero vector alone
    assert [0, 2] in member_sets  # the two 0° texts merged across the zero vector


def test_empty_vector_isolated():
    groups = group_semantic(["a", "e"], embed_fn=_fixed([_unit(0), []]))
    assert sorted(sorted(g.member_indices) for g in groups) == [[0], [1]]


def test_order_is_deterministic_by_first_appearance():
    groups = group_semantic(
        ["a", "b", "c", "d"],
        embed_fn=_fixed([_unit(0), _unit(90), _unit(0), _unit(90)]),
    )
    # Two groups, representatives in first-appearance order (0 then 1).
    assert [g.representative_index for g in groups] == [0, 1]
    assert sorted(groups[0].member_indices) == [0, 2]
    assert sorted(groups[1].member_indices) == [1, 3]
