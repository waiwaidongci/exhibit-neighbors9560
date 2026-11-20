"""Exact exhibit ranking from pairwise visitor votes.

Each visitor votes once for every pair of exhibits they rated with different
stars: the higher-star exhibit should come first.  Equal ratings and missing
ratings produce no vote.

The cheapest complete order (fewest violated votes) is found with a subset
dynamic program over 2**n states, never by enumerating permutations.

For every pair of exhibits we then ask whether both relative orders are
achievable at that same optimal cost.  Such pairs are reported as undetermined
and two equally optimal complete orders are reconstructed as evidence.

Only the Python standard library is used so the worker can import this module
without the API layer.
"""
from array import array
from collections import defaultdict
from itertools import combinations
import hashlib
import json

INF = 10 ** 12
MAX_EXHIBITS = 16


def normalize_ratings(ratings):
    """One rating per (visitor, exhibit), last entry winning.

    Returns a canonical list sorted by visitor then exhibit.  The caller is
    expected to pass records in entry order (database id order).
    """
    latest = {}
    for item in ratings:
        latest[(str(item['visitor']), str(item['exhibit']))] = float(item['rating'])
    return [
        {'visitor': visitor, 'exhibit': exhibit, 'rating': rating}
        for (visitor, exhibit), rating in sorted(latest.items())
    ]


def pair_votes_from(ratings):
    """Aggregated votes ``(winner, loser) -> count`` for normalized ratings."""
    by_visitor = defaultdict(dict)
    for item in ratings:
        by_visitor[item['visitor']][item['exhibit']] = item['rating']
    counts = defaultdict(int)
    for items in by_visitor.values():
        for left, right in combinations(sorted(items), 2):
            left_rating, right_rating = items[left], items[right]
            if left_rating == right_rating:
                continue
            if left_rating > right_rating:
                counts[(left, right)] += 1
            else:
                counts[(right, left)] += 1
    return dict(counts)


def count_violations(order, pair_votes):
    """Count votes whose winner appears after the loser in ``order``."""
    position = {code: index for index, code in enumerate(order)}
    return sum(count for (winner, loser), count in pair_votes.items()
               if position[winner] > position[loser])


def _subset_tables(matrix, n):
    """Subset-sum tables used by the dynamic program.

    ``prefix[x][mask]`` = votes wanting ``x`` before some exhibit in ``mask``.
    ``column[x][mask]`` = votes wanting some exhibit in ``mask`` before ``x``.
    """
    size = 1 << n
    prefix = [array('i', [0]) * size for _ in range(n)]
    column = [array('i', [0]) * size for _ in range(n)]
    for x in range(n):
        row = matrix[x]
        px, cx = prefix[x], column[x]
        for mask in range(1, size):
            bit = mask & -mask
            y = bit.bit_length() - 1
            previous = mask ^ bit
            px[mask] = px[previous] + row[y]
            cx[mask] = cx[previous] + matrix[y][x]
    return prefix, column


def rank(selected, pair_votes, *, visitors=0, ratings_used=0, checksum='', progress=None):
    """Return the complete ranking report for the frozen ``pair_votes``.

    ``pair_votes`` maps ``(before, after) -> vote count`` and must be frozen
    before this call so later ratings cannot alter the study.  ``progress`` is
    an optional ``(percent, stage)`` callback.
    """
    selected = sorted({str(code).strip() for code in selected if str(code).strip()})
    if len(selected) < 2:
        raise ValueError('至少选择2件展品才能排位')
    if len(selected) > MAX_EXHIBITS:
        raise ValueError('最多选择16件展品')

    chosen = set(selected)
    for winner, loser in pair_votes:
        if winner not in chosen or loser not in chosen:
            raise ValueError('封存票数包含选品之外的展品')

    n = len(selected)
    index = {code: i for i, code in enumerate(selected)}
    matrix = [[0] * n for _ in range(n)]
    for (winner, loser), count in pair_votes.items():
        matrix[index[winner]][index[loser]] = int(count)
    total_votes = sum(pair_votes.values())
    if progress:
        progress(45, '已还原封存的两两投票')

    size = 1 << n
    full = size - 1
    prefix, column = _subset_tables(matrix, n)

    # dp_pre[mask]: fewest violated votes among exhibits in `mask` when they
    # occupy the leading positions, counting only votes with both endpoints in
    # `mask`.  Placing x last among `mask` violates every vote wanting x first.
    dp_pre = [INF] * size
    dp_pre[0] = 0
    for mask in range(1, size):
        best = INF
        remaining = mask
        while remaining:
            bit = remaining & -remaining
            x = bit.bit_length() - 1
            remaining ^= bit
            value = dp_pre[mask ^ bit] + prefix[x][mask ^ bit]
            if value < best:
                best = value
        dp_pre[mask] = best

    # Every complete order splits at a cut S (leading block) / complement
    # (trailing block).  Cross votes always place leading exhibits first, so
    #   cost(S) = dp_pre[S] + dp_pre[full ^ S] + cross_votes(S)
    # and the overall optimum is the minimum over all cuts.
    lead_votes = [sum(matrix[y][x] for y in range(n)) for x in range(n)]
    cross_votes = [0] * size
    for mask in range(1, size):
        bit = mask & -mask
        x = bit.bit_length() - 1
        previous = mask ^ bit
        cross_votes[mask] = (cross_votes[previous] + lead_votes[x]
                             - prefix[x][previous] - column[x][previous])

    cost = [dp_pre[mask] + dp_pre[full ^ mask] + cross_votes[mask]
            for mask in range(size)]

    optimum = dp_pre[full]
    if cost[full] != optimum or min(cost) != optimum:
        raise RuntimeError('排位动态规划切分复核不一致')
    if progress:
        progress(70, '已求得最少违背票数')

    def reconstruct(mask):
        """Cheapest ordering of the exhibits in `mask` (lowest index on ties)."""
        sequence = []
        while mask:
            best = INF
            chosen_index = -1
            remaining = mask
            while remaining:
                bit = remaining & -remaining
                x = bit.bit_length() - 1
                remaining ^= bit
                value = dp_pre[mask ^ bit] + prefix[x][mask ^ bit]
                if value < best or (value == best and x < chosen_index):
                    best = value
                    chosen_index = x
            sequence.append(chosen_index)
            mask ^= 1 << chosen_index
        sequence.reverse()
        return [selected[i] for i in sequence]

    def order_from_cut(mask):
        return reconstruct(mask) + reconstruct(full ^ mask)

    primary = order_from_cut(min(range(size), key=lambda mask: (cost[mask], mask)))

    ambiguous = []
    example_cuts = None
    for i in range(n):
        for j in range(i + 1, n):
            a_bit, b_bit = 1 << i, 1 << j
            rest = full ^ a_bit ^ b_bit
            best_ab = best_ba = INF
            cut_ab = cut_ba = None
            sub = rest
            while True:
                mask_ab = sub | a_bit
                if cost[mask_ab] < best_ab:
                    best_ab = cost[mask_ab]
                    cut_ab = mask_ab
                mask_ba = sub | b_bit
                if cost[mask_ba] < best_ba:
                    best_ba = cost[mask_ba]
                    cut_ba = mask_ba
                if sub == 0:
                    break
                sub = (sub - 1) & rest
            order_a = best_ab == optimum
            order_b = best_ba == optimum
            if order_a and order_b:
                ambiguous.append([selected[i], selected[j]])
                if example_cuts is None:
                    example_cuts = (cut_ab, cut_ba)
            elif not order_a and not order_b:
                raise RuntimeError('排位动态规划未能为重排对找到最优次序')

    if progress:
        progress(90, '已核对全部展品对的先后')

    example_pair = None
    example_differences = []
    if example_cuts:
        first_order = order_from_cut(example_cuts[0])
        second_order = order_from_cut(example_cuts[1])
        checked_first = count_violations(first_order, pair_votes)
        checked_second = count_violations(second_order, pair_votes)
        if checked_first != optimum or checked_second != optimum:
            raise RuntimeError('重建的同样最优次序未通过违背票数复核')
        examples = [
            {'order': first_order, 'violations': checked_first},
            {'order': second_order, 'violations': checked_second},
        ]
        primary = first_order
        example_pair = ambiguous[0]
        first_position = {code: i for i, code in enumerate(first_order)}
        second_position = {code: i for i, code in enumerate(second_order)}
        for x in range(n):
            for y in range(x + 1, n):
                a, b = selected[x], selected[y]
                if (first_position[a] < first_position[b]) != (second_position[a] < second_position[b]):
                    example_differences.append([a, b])
    else:
        examples = [{'order': primary, 'violations': count_violations(primary, pair_votes)}]

    checked_primary = count_violations(primary, pair_votes)
    if checked_primary != optimum:
        raise RuntimeError('主次序未通过违背票数复核')

    vote_counts = [
        {'before': winner, 'after': loser, 'count': int(count)}
        for (winner, loser), count in sorted(pair_votes.items())
    ]
    undecided_exhibits = sorted({code for pair in ambiguous for code in pair})

    return {
        'method': 'Kemeny 子集动态规划：2^n 状态求最少违背票数，不枚举全部排列',
        'selected': selected,
        'order': primary,
        'violations': optimum,
        'total_votes': total_votes,
        'visitors': visitors,
        'ratings_used': ratings_used,
        'vote_counts': vote_counts,
        'ambiguous_pairs': ambiguous,
        'ambiguous_count': len(ambiguous),
        'undecided_exhibits': undecided_exhibits,
        'decided_pair_count': n * (n - 1) // 2 - len(ambiguous),
        'example_pair': example_pair,
        'example_differences': example_differences,
        'examples': examples,
        'verification': {
            'ok': checked_primary == optimum and all(
                count_violations(example['order'], pair_votes) == optimum
                for example in examples
            ),
            'minimum_violations': optimum,
            'recomputed_primary': checked_primary,
            'recomputed_examples': [
                count_violations(example['order'], pair_votes) for example in examples
            ],
            'total_votes': total_votes,
        },
        'checksum': checksum,
        'seed': 42,
    }


def solve(selected, ratings, progress=None):
    """Convenience entry point that normalizes raw ratings before ranking.

    Duplicate (visitor, exhibit) ratings resolve to the last entry; ratings for
    exhibits outside the selection are ignored.
    """
    chosen = {str(code).strip() for code in selected}
    normalized = [
        item for item in normalize_ratings(ratings) if item['exhibit'] in chosen
    ]
    pair_votes = pair_votes_from(normalized)
    checksum = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return rank(
        selected, pair_votes,
        visitors=len({item['visitor'] for item in normalized}),
        ratings_used=len(normalized),
        checksum=checksum,
        progress=progress,
    )
