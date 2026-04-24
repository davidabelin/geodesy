from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "coverage" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


from coverage_solver import CoverCandidate, build_set_cover_graph, solve_networkx_full_cover


def test_set_cover_graph_contains_expected_nodes_and_edges() -> None:
    candidates = [
        CoverCandidate("site-a", frozenset({"A", "B"})),
        CoverCandidate("site-b", frozenset({"B", "C"})),
    ]

    graph = build_set_cover_graph(["A", "B", "C"], candidates)

    root = "__root__"
    candidate_nodes = [
        node for node, attrs in graph.nodes(data=True) if attrs.get("kind") == "candidate"
    ]
    assert root in graph
    assert {graph.nodes[node]["candidate_id"] for node in candidate_nodes} == {
        "site-a",
        "site-b",
    }
    assert {"point:A", "point:B", "point:C"}.issubset(graph.nodes)
    assert all(graph.has_edge(root, node) for node in candidate_nodes)
    site_a_node = next(
        node for node in candidate_nodes if graph.nodes[node]["candidate_id"] == "site-a"
    )
    assert graph.has_edge(site_a_node, "point:A")
    assert graph.has_edge(site_a_node, "point:B")
    assert not graph.has_edge(site_a_node, "point:C")


def test_networkx_solution_covers_requested_universe() -> None:
    result = solve_networkx_full_cover(
        ["A", "B", "C"],
        [
            CoverCandidate("all", frozenset({"A", "B", "C"}), weight=0.999),
            CoverCandidate("left", frozenset({"A", "B"}), weight=1.0),
            CoverCandidate("right", frozenset({"B", "C"}), weight=1.0),
        ],
    )

    assert result.selected_ids == ("all",)
    assert result.covered_ids == frozenset({"A", "B", "C"})
    assert result.uncovered_ids == frozenset()
    assert result.is_complete


def test_networkx_solution_removes_redundant_candidates_deterministically() -> None:
    result = solve_networkx_full_cover(
        ["A", "B", "C"],
        [
            CoverCandidate("all", frozenset({"A", "B", "C"}), weight=0.999),
            CoverCandidate("also-all", frozenset({"A", "B", "C"}), weight=1.0),
            CoverCandidate("left", frozenset({"A", "B"}), weight=1.0),
            CoverCandidate("right", frozenset({"B", "C"}), weight=1.0),
        ],
    )

    assert result.selected_ids == ("all",)


def test_networkx_solution_reports_uncovered_points() -> None:
    result = solve_networkx_full_cover(
        ["A", "B", "C"],
        [CoverCandidate("site-a", frozenset({"A"}))],
    )

    assert result.selected_ids == ("site-a",)
    assert result.covered_ids == frozenset({"A"})
    assert result.uncovered_ids == frozenset({"B", "C"})
