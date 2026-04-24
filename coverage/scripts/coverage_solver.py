from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
from networkx.algorithms import approximation


ROOT_NODE = "__root__"
POINT_WEIGHT = 1_000_000.0
ROOT_WEIGHT = 1e-12


@dataclass(frozen=True)
class CoverCandidate:
    candidate_id: str
    covered_ids: frozenset[str]
    weight: float = 1.0
    sort_key: tuple[Any, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", str(self.candidate_id))
        object.__setattr__(
            self,
            "covered_ids",
            frozenset(str(point_id) for point_id in self.covered_ids),
        )
        object.__setattr__(self, "weight", float(self.weight))
        if not self.sort_key:
            object.__setattr__(self, "sort_key", (self.candidate_id,))


@dataclass(frozen=True)
class CoverSolution:
    selected_ids: tuple[str, ...]
    covered_ids: frozenset[str]
    uncovered_ids: frozenset[str]
    stage: str
    graph: nx.Graph

    @property
    def is_complete(self) -> bool:
        return not self.uncovered_ids


def build_set_cover_graph(
    universe_ids: Iterable[str],
    candidates: Iterable[CoverCandidate],
) -> nx.Graph:
    """Build the NetworkX model used for set-cover approximation.

    The graph is a root-plus-bipartite representation: candidate nodes connect
    to the point nodes they cover, and the root connects to every candidate.
    NetworkX's weighted dominating-set approximation then acts as a practical
    set-cover approximation while remaining independent of the geometry that
    produced each candidate.
    """

    universe = tuple(str(point_id) for point_id in universe_ids)
    candidate_list = tuple(candidates)
    graph = nx.Graph()
    graph.add_node(ROOT_NODE, kind="root", weight=ROOT_WEIGHT)

    for point_id in sorted(universe):
        graph.add_node(_point_node(point_id), kind="point", label=point_id, weight=POINT_WEIGHT)

    for index, candidate in enumerate(candidate_list):
        node = _candidate_node(index)
        graph.add_node(
            node,
            kind="candidate",
            candidate=candidate,
            candidate_id=candidate.candidate_id,
            label=candidate.candidate_id,
            weight=candidate.weight,
        )
        graph.add_edge(ROOT_NODE, node)
        for point_id in sorted(candidate.covered_ids):
            point_node = _point_node(point_id)
            if point_node in graph:
                graph.add_edge(node, point_node)

    return graph


def solve_networkx_full_cover(
    universe_ids: Iterable[str],
    candidates: Iterable[CoverCandidate],
) -> CoverSolution:
    """Approximate a full set cover with NetworkX.

    The solver selects candidate subsets that cover as many universe IDs as
    possible, then removes redundant selected candidates deterministically. It
    reports uncovered IDs rather than failing when the candidate pool cannot
    cover the entire requested universe.
    """

    universe = frozenset(str(point_id) for point_id in universe_ids)
    candidate_list = tuple(_dedupe_candidates(candidates))
    graph = build_set_cover_graph(universe, candidate_list)
    if not universe or not candidate_list:
        return CoverSolution(
            selected_ids=(),
            covered_ids=frozenset(),
            uncovered_ids=universe,
            stage="networkx",
            graph=graph,
        )

    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidate_list}
    dominating_nodes = approximation.min_weighted_dominating_set(graph, weight="weight")
    selected = [
        graph.nodes[node]["candidate"]
        for node in dominating_nodes
        if graph.nodes[node].get("kind") == "candidate"
    ]
    selected = _remove_redundant_candidates(selected, universe)
    selected = sorted(selected, key=_candidate_order_key)
    selected_ids = tuple(candidate.candidate_id for candidate in selected)
    covered = frozenset().union(
        *(candidate_by_id[candidate_id].covered_ids for candidate_id in selected_ids)
    ) if selected_ids else frozenset()

    return CoverSolution(
        selected_ids=selected_ids,
        covered_ids=covered & universe,
        uncovered_ids=universe - covered,
        stage="networkx",
        graph=graph,
    )


def _dedupe_candidates(candidates: Iterable[CoverCandidate]) -> tuple[CoverCandidate, ...]:
    best_by_id: dict[str, CoverCandidate] = {}
    for candidate in candidates:
        existing = best_by_id.get(candidate.candidate_id)
        if existing is None or _candidate_order_key(candidate) < _candidate_order_key(existing):
            best_by_id[candidate.candidate_id] = candidate
    return tuple(sorted(best_by_id.values(), key=_candidate_order_key))


def _remove_redundant_candidates(
    candidates: Iterable[CoverCandidate],
    universe: frozenset[str],
) -> list[CoverCandidate]:
    selected = sorted(candidates, key=_candidate_order_key)

    changed = True
    while changed:
        changed = False
        for candidate in sorted(selected, key=_redundancy_order_key):
            proposal = [item for item in selected if item.candidate_id != candidate.candidate_id]
            covered = _covered_ids(proposal) & universe
            if covered == (_covered_ids(selected) & universe):
                selected = proposal
                changed = True
                break

    return selected


def _covered_ids(candidates: Iterable[CoverCandidate]) -> frozenset[str]:
    candidate_list = tuple(candidates)
    if not candidate_list:
        return frozenset()
    return frozenset().union(*(candidate.covered_ids for candidate in candidate_list))


def _candidate_order_key(candidate: CoverCandidate) -> tuple[Any, ...]:
    return (
        round(candidate.weight, 12),
        -len(candidate.covered_ids),
        candidate.sort_key,
        candidate.candidate_id,
    )


def _redundancy_order_key(candidate: CoverCandidate) -> tuple[Any, ...]:
    return (
        len(candidate.covered_ids),
        -round(candidate.weight, 12),
        candidate.sort_key,
        candidate.candidate_id,
    )


def _point_node(point_id: str) -> str:
    return f"point:{point_id}"


def _candidate_node(index: int) -> str:
    return f"candidate:{index}"
