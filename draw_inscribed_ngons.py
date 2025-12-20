import argparse
import itertools
import math
from typing import Iterable, List, Sequence, Tuple

import matplotlib.pyplot as plt


def polygon_points(n: int, radius: float, phase: float = math.pi / 2) -> List[Tuple[float, float]]:
    """Return x,y points for a regular n-gon on a circle of given radius."""
    step = 2 * math.pi / n
    return [(radius * math.cos(phase + i * step), radius * math.sin(phase + i * step)) for i in range(n)]


def side_length(n: int, radius: float) -> float:
    """Side length of a regular n-gon inscribed in a circle of radius r."""
    return 2 * radius * math.sin(math.pi / n)


def draw_ngons(
    radius: float,
    n_values: Sequence[int],
    show_labels: bool = False,
    show_diagonals: bool = False,
) -> None:
    _, ax = plt.subplots(figsize=(10,10))
    ax.set_aspect("equal")
    ax.set_title(f"Inscribed regular n-gons (r = {radius})")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.grid(True, linewidth=0.4, alpha=0.5)

    # Outer circle
    circle = plt.Circle((0, 0), radius, color="gray", fill=False, linewidth=1.0, alpha=0.9)
    ax.add_artist(circle)

    color_cycle: Iterable[str] = itertools.cycle(plt.rcParams["axes.prop_cycle"].by_key()["color"])
    for n in n_values:
        points = polygon_points(n, radius)
        x, y = zip(*(points + [points[0]]))  # close polygon
        color = next(color_cycle)
        #color = "darkcyan"
        ax.plot(x, y, color=color, alpha=0.8, linewidth=0.8, label=f"n={n}")

        if show_diagonals:
            lw_diag = 0.5
            for i in range(n):
                for j in range(i + 1, n):
                    x_pair = (points[i][0], points[j][0])
                    y_pair = (points[i][1], points[j][1])
                    ax.plot(x_pair, y_pair, color=color, linewidth=lw_diag, alpha=0.9)

        s = side_length(n, radius)
        if show_labels:
            cx = sum(p[0] for p in points) / n
            cy = sum(p[1] for p in points) / n
            ax.text(
                cx,
                cy,
                f"n={n}\ns={s:.3f}",
                color=color,
                ha="center",
                va="center",
                fontsize=8,
                bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.6),
            )

        print(f"n={n:2d}  s=2r sin(pi/{n:02d}) = {s:.6f}")

    ax.legend(loc="upper right")
    ax.set_xlim(-1.1 * radius, 1.1 * radius)
    ax.set_ylim(-1.1 * radius, 1.1 * radius)
    plt.tight_layout()
    plt.show()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Draw regular n-gons cocircumscribed in a circle of radius r."
    )
    parser.add_argument("r", type=float, help="radius of the circle (in pixels or chosen units)")
    parser.add_argument(
        "n_values",
        type=int,
        nargs="*",
        help="list of n values for the n-gons; defaults to 4 6 10 when omitted",
    )
    parser.add_argument(
        "--labels",
        action="store_true",
        help="overlay polygon labels (off by default to avoid clutter)",
    )
    parser.add_argument(
        "--diagonals",
        action="store_true",
        help="draw all vertex-to-vertex diagonals for each n-gon (thin lines)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    r = args.r or 100.0
    n_values = args.n_values or [4, 6, 10]
    print(f"Circle radius r = {r} px, n-gons: {n_values}")
    for n in n_values:
        if n < 3:
            raise ValueError(f"n must be >= 3 (got {n})")
    draw_ngons(r, n_values, show_labels=args.labels, show_diagonals=args.diagonals)


if __name__ == "__main__":
    main()
