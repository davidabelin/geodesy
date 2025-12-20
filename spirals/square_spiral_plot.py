import argparse
import math
from dataclasses import dataclass
from typing import List, Tuple, Dict

import matplotlib.pyplot as plt
from matplotlib.patches import Arc, Rectangle


# Color pairs (square fill = light, arc + center = dark)
COLOR_PALETTE: List[Dict[str, str]] = [
    {"light_name": "light pink", "light_hex": "#f9c2c2", "dark_name": "dark pink", "dark_hex": "#c27a7a"},
    {"light_name": "light yellow", "light_hex": "#f9e0a8", "dark_name": "dark yellow", "dark_hex": "#c29a43"},
    {"light_name": "light green", "light_hex": "#b3e6b3", "dark_name": "dark green", "dark_hex": "#5ba65b"},
    {"light_name": "light blue", "light_hex": "#a7c6ff", "dark_name": "dark blue", "dark_hex": "#4a70c2"},
    {"light_name": "light cyan", "light_hex": "#c2f1ff", "dark_name": "dark cyan", "dark_hex": "#5da3b5"},
    {"light_name": "light purple", "light_hex": "#e0c2ff", "dark_name": "dark purple", "dark_hex": "#8f5ab8"},
]


@dataclass(frozen=True)
class Square:
    """Axis-aligned square (x,y is lower-left)."""
    x: float
    y: float
    size: float
    direction: int  # 0=E,1=N,2=W,3=S (placement direction for this square)


def fibonacci_numbers(n: int, base_size: float = 1.0) -> List[float]:
    """Return the first n Fibonacci numbers scaled by base_size (1, 1, 2, 3, ...)."""
    if n <= 0:
        return []
    if n == 1:
        return [base_size]
    fibs = [base_size, base_size]
    for _ in range(2, n):
        fibs.append(fibs[-1] + fibs[-2])
    return fibs


def build_squares(n: int, base_size: float = 1.0, ccw: bool = True) -> Tuple[List[Square], List[float]]:
    """
    Build a Fibonacci tiling of n squares by expanding a bounding rectangle.

    Directions cycle (CCW): E -> N -> W -> S -> ...
    """
    if n <= 0:
        return [], []

    fib_sizes = fibonacci_numbers(n, base_size)
    fib_counts = fibonacci_numbers(n, 1.0)

    squares: List[Square] = []
    dir_idx = 0  # square0 direction
    minx = miny = 0.0
    maxx = maxy = fib_sizes[0]
    squares.append(Square(x=0.0, y=0.0, size=fib_sizes[0], direction=dir_idx))

    step = 1 if ccw else -1
    for i in range(1, n):
        dir_idx = (dir_idx + step) % 4
        s = fib_sizes[i]
        if dir_idx == 0:  # E
            x = maxx
            y = miny
            maxx += s
        elif dir_idx == 1:  # N
            x = minx
            y = maxy
            maxy += s
        elif dir_idx == 2:  # W
            x = minx - s
            y = miny
            minx -= s
        else:  # S
            x = minx
            y = miny - s
            miny -= s
        squares.append(Square(x=x, y=y, size=s, direction=dir_idx))

    return squares, fib_counts


def arc_center_and_angles(sq: Square, ccw: bool) -> Tuple[Tuple[float, float], float, float]:
    """
    Return (center_x, center_y), theta1, theta2 for the quarter-circle arc in a square.

    User-provided CCW scheme (angles are endpoints; we draw the minor arc):
      dir 0 (E): center = lower-right, sweep 180 -> 90
      dir 1 (N): center = lower-left,  sweep 90  -> 0
      dir 2 (W): center = upper-left,  sweep 360 -> 270
      dir 3 (S): center = upper-right, sweep 270 -> 180
    """
    def minor_arc_ccw(a: float, b: float) -> Tuple[float, float]:
        a = a % 360.0
        b = b % 360.0
        return (b, a) if (b - a) % 360.0 > 180.0 else (a, b)

    x, y, s, d = sq.x, sq.y, sq.size, sq.direction
    if d == 0:  # E
        cx, cy = x + s, y  # lower-right
        th1, th2 = minor_arc_ccw(180.0, 90.0)
    elif d == 1:  # N
        cx, cy = x, y  # lower-left
        th1, th2 = minor_arc_ccw(90.0, 0.0)
    elif d == 2:  # W
        cx, cy = x, y + s  # upper-left
        th1, th2 = minor_arc_ccw(360.0, 270.0)
    else:  # S
        cx, cy = x + s, y + s  # upper-right
        th1, th2 = minor_arc_ccw(270.0, 180.0)

    if not ccw:
        th1, th2 = th2, th1
    return (cx, cy), th1, th2


def draw_fib_spiral(
    ax,
    squares: List[Square],
    fib_counts: List[float],
    ccw: bool,
    *,
    edge_color: str = "k",
    edge_width: float = 1.0,
    arc_width: float = 2.0,
    show_labels: bool = True,
    show_centers: bool = True,
) -> None:
    """Draw the squares plus the quarter-circle arcs (and optional centers)."""
    for i, sq in enumerate(squares):
        palette = COLOR_PALETTE[i % len(COLOR_PALETTE)]
        rect = Rectangle(
            (sq.x, sq.y),
            sq.size,
            sq.size,
            facecolor=palette["light_hex"],
            edgecolor=edge_color,
            linewidth=edge_width,
        )
        ax.add_patch(rect)

        if show_labels:
            ax.text(
                sq.x + sq.size * 0.5,
                sq.y + sq.size * 0.5,
                f"{int(fib_counts[i])}",
                ha="center",
                va="center",
                fontsize=14,
                color="k",
            )

    for i, sq in enumerate(squares):
        palette = COLOR_PALETTE[i % len(COLOR_PALETTE)]
        dark = palette["dark_hex"]
        center, th1, th2 = arc_center_and_angles(sq, ccw=ccw)
        ax.add_patch(
            Arc(
                center,
                width=2 * sq.size,
                height=2 * sq.size,
                theta1=th1,
                theta2=th2,
                color=dark,
                lw=arc_width,
            )
        )
        if show_centers:
            ax.plot(center[0], center[1], marker="o", color=dark, markersize=4)


def main() -> None:
    ap = argparse.ArgumentParser(description="Fibonacci square spiral (quarter-circle approximation)")
    ap.add_argument("--terms", type=int, default=8, help="How many Fibonacci squares/arcs to draw")
    ap.add_argument("--base", type=float, default=1.0, help="Side length of the first square (F1)")
    ap.add_argument("--cw", action="store_true", help="Draw clockwise instead of counter-clockwise")
    ap.add_argument("--no-labels", action="store_true", help="Hide Fibonacci number labels")
    ap.add_argument("--no-centers", action="store_true", help="Hide arc center markers")
    ap.add_argument("--save", type=str, default="", help="Path to save PNG (optional)")
    ap.add_argument("--show", action="store_true", help="Display the figure in a window")
    ap.add_argument("--pad", type=float, default=0.05, help="Padding fraction relative to total span")
    args = ap.parse_args()

    ccw = not args.cw
    squares, fib_counts = build_squares(args.terms, base_size=args.base, ccw=ccw)

    fig, ax = plt.subplots(figsize=(8, 5))
    draw_fib_spiral(
        ax,
        squares,
        fib_counts,
        ccw=ccw,
        edge_color="k",
        edge_width=1.0,
        arc_width=2.0,
        show_labels=not args.no_labels,
        show_centers=not args.no_centers,
    )

    # Bounds
    xs = [p for sq in squares for p in (sq.x, sq.x + sq.size)]
    ys = [p for sq in squares for p in (sq.y, sq.y + sq.size)]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    pad = args.pad * max(xmax - xmin, ymax - ymin)
    ax.set_xlim(xmin - pad, xmax + pad)
    ax.set_ylim(ymin - pad, ymax + pad)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")

    if args.save:
        fig.savefig(args.save, dpi=220, bbox_inches="tight", facecolor="white")
    if args.show or not args.save:
        plt.show()


if __name__ == "__main__":
    main()
