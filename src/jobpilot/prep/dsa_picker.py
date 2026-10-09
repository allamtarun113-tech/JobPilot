"""DSA picker: map the job's topics and tags to problem patterns.

Each pattern comes with an approach and complexity. Matches the
planner's topic names and skill ids against pattern keywords; when the
job only says "DSA" / "algorithms", a core set of the most-asked
patterns is used. Deterministic - no model call.
"""

from __future__ import annotations

import re

from jobpilot.models.prep import Topic

# id: (pattern, approach, complexity, keywords)
PATTERNS: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "two-pointers": (
        "Two pointers",
        "Move two indices over sorted data or from both ends; each step discards candidates.",
        "O(n) time, O(1) space",
        ("two pointer", "array", "sorted", "pair"),
    ),
    "sliding-window": (
        "Sliding window",
        "Grow the right edge, shrink the left while the window breaks the invariant.",
        "O(n) time, O(k) space",
        ("sliding window", "substring", "subarray", "string"),
    ),
    "prefix-sums": (
        "Prefix sums / difference arrays",
        "Precompute running totals so any range query is one subtraction.",
        "O(n) build, O(1) query",
        ("prefix", "range sum", "cumulative", "array"),
    ),
    "hashing": (
        "Hash map counting / lookup",
        "Trade memory for O(1) lookups: counts, complements, first-seen indices.",
        "O(n) time, O(n) space",
        ("hash", "dictionary", "frequency", "anagram", "duplicate"),
    ),
    "binary-search": (
        "Binary search on index or answer",
        "Prove monotonic feasibility, then search the boundary.",
        "O(log n) or O(n log R)",
        ("binary search", "sorted", "search", "minimum maximum"),
    ),
    "sorting-intervals": (
        "Sort then sweep (intervals)",
        "Sort by start, merge or count overlaps in one pass.",
        "O(n log n) time",
        ("interval", "meeting", "schedule", "merge", "sort"),
    ),
    "stack": (
        "Monotonic stack",
        "Keep a stack ordered so each element is pushed and popped once.",
        "O(n) time, O(n) space",
        ("stack", "parenthes", "next greater", "histogram"),
    ),
    "linked-list": (
        "Fast / slow pointers on linked lists",
        "Two pointers at different speeds find middles, cycles and k-th from end.",
        "O(n) time, O(1) space",
        ("linked list", "cycle"),
    ),
    "heap": (
        "Heap / top-k",
        "Keep a size-k heap; pop the smallest when it overflows.",
        "O(n log k) time",
        ("heap", "priority queue", "top k", "kth", "median", "stream"),
    ),
    "trees": (
        "Tree DFS / BFS",
        "Recurse with what each subtree returns; BFS by levels with a queue.",
        "O(n) time, O(h) space",
        ("tree", "bst", "binary tree", "traversal", "recurs"),
    ),
    "graphs": (
        "Graph BFS / DFS",
        "Model nodes and edges, mark visited, traverse components; BFS for shortest unweighted paths.",
        "O(V + E)",
        ("graph", "network", "grid", "island", "shortest path", "bfs", "dfs"),
    ),
    "topological-sort": (
        "Topological sort",
        "Kahn's algorithm: repeatedly take nodes with in-degree zero.",
        "O(V + E)",
        ("dependency", "prerequisite", "topolog", "dag", "scheduling", "build order"),
    ),
    "union-find": (
        "Union-Find",
        "Union by rank + path compression for dynamic connectivity.",
        "~O(α(n)) per operation",
        ("union find", "disjoint", "connected", "component"),
    ),
    "dijkstra": (
        "Dijkstra / weighted shortest path",
        "Min-heap of (distance, node); relax edges.",
        "O((V + E) log V)",
        ("weighted", "dijkstra", "route", "cost path"),
    ),
    "dynamic-programming": (
        "State-transition DP",
        "Define state, transition, base cases and iteration order; memoize.",
        "O(states x transitions)",
        ("dynamic programming", "dp", "knapsack", "subsequence", "optimi", "count ways"),
    ),
    "backtracking": (
        "Backtracking",
        "Choose, explore, un-choose; prune branches that can't succeed.",
        "Exponential; prune early",
        ("backtrack", "permutation", "combination", "subset", "n-queen"),
    ),
    "greedy": (
        "Greedy with an exchange argument",
        "Sort by the right key, take the locally best choice, prove it with an exchange argument.",
        "O(n log n)",
        ("greedy", "activity", "minimum number", "jump"),
    ),
    "bit-manipulation": (
        "Bit manipulation",
        "XOR cancels pairs; masks enumerate subsets.",
        "O(n) or O(2^n) for subsets",
        ("bit", "xor", "mask", "binary representation"),
    ),
    "trie": (
        "Trie",
        "Insert words character by character; share prefixes.",
        "O(total characters)",
        ("trie", "prefix", "autocomplete", "dictionary of words"),
    ),
    "sql": (
        "Relational query decomposition",
        "Build joins and filters first, then aggregate or window in stages.",
        "Plan dependent; index the join keys",
        ("sql", "query", "join", "window function", "database"),
    ),
}
CORE = (
    "hashing",
    "two-pointers",
    "sliding-window",
    "binary-search",
    "trees",
    "graphs",
    "dynamic-programming",
    "heap",
)
BROAD = {"dsa", "algorithms", "data-structures", "competitive-programming"}
MAX_PATTERNS = 8


def pick(topics: list[Topic], gaps: list[dict], jd_text: str = "") -> list[dict[str, str]]:
    """Pick up to MAX_PATTERNS problem patterns for a job.

    Args:
        topics: The prep topics (names, reasons, skill ids).
        gaps: The job's gap items.
        jd_text: The JD (first 4,000 characters are searched).

    Returns:
        Patterns with their approach and complexity, best match first.
    """
    skills = {skill for topic in topics for skill in topic.skill_ids}
    skills.update(str(gap.get("skill_id") or "") for gap in gaps)
    text = " ".join([*(t.name for t in topics), *(t.reason for t in topics), jd_text[:4000]]).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    scored: list[tuple[int, str]] = []
    for pid, (_, _, _, keywords) in PATTERNS.items():
        # Keyword hits in the text, plus a strong bonus for a tagged
        # skill id.
        hits = sum(1 for k in keywords if f" {k}" in f" {text}") + (3 if pid in skills else 0)
        if hits:
            scored.append((hits, pid))
    chosen = [pid for _, pid in sorted(scored, key=lambda x: (-x[0], x[1]))]
    if skills & BROAD or any(t.category == "dsa" for t in topics):
        chosen += [pid for pid in CORE if pid not in chosen]
    return [
        {
            "skill_id": pid,
            "pattern": PATTERNS[pid][0],
            "approach": PATTERNS[pid][1],
            "complexity": PATTERNS[pid][2],
        }
        for pid in chosen[:MAX_PATTERNS]
    ]
