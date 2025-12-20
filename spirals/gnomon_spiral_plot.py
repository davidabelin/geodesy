import math
import argparse
import cmath
from dataclasses import dataclass
from typing import List, Tuple
import matplotlib.pyplot as plt
from matplotlib.patches import Arc

# Golden ratio
PHI = (1 + 5 ** 0.5) / 2.0


@dataclass
class Gnomon:
    apex: complex
    base0: complex
    base1: complex
    side_len: float  # equal sides length (base length = PHI * side_len)


def angle_deg(center: complex, p: complex) -> float:
    v = p - center
    return math.degrees(math.atan2(v.imag, v.real))


def minor_arc(a1: float, a2: float) -> Tuple[float, float]:
    """Return start/end angles (degrees) for the shorter CCW arc between a1 and a2."""
    def norm(a: float) -> float:
        a = a % 360.0
        return a + 360.0 if a < 0 else a
    a1n = norm(a1)
    a2n = norm(a2)
    d = (a2n - a1n) % 360.0
    if d <= 180.0:
        return a1n, a2n
    # swap to keep sweep under 180
    return a2n, a1n


def build_gnomon_chain(steps: int, base_len: float, ccw: bool) -> List[Gnomon]:
    """
    Build an outward-growing chain where each long side becomes
    the next gnomon's equal side. Angles turn by 36° each step.
    """
    gnomons: List[Gnomon] = []
    sign = 1.0 if ccw else -1.0  # controls turn direction
    phi_apex = math.radians(sign * 108.0)  # angle between equal sides

    # Start with equal side length s0 so base0 length is base_len
    s = base_len / PHI
    apex = 0 + 0j
    e_vec = s  # equal side vector along +x from apex to first base point

    for _ in range(steps):
        e_vec2 = e_vec * cmath.exp(1j * phi_apex)  # rotate by ±108°
        base0 = apex + e_vec
        base1 = apex + e_vec2
        gnomons.append(Gnomon(apex=apex, base0=base0, base1=base1, side_len=abs(e_vec)))

        # Next gnomon: apex moves to base0; equal side becomes the previous long side (base segment)
        base_vec = base1 - base0  # this is the long side; length = PHI * |e_vec|
        apex = base0
        e_vec = base_vec  # reuse long side as the next equal side

    return gnomons


def draw_spiral(ax,
                gnomons: List[Gnomon],
                line_color: str = 'k',
                line_width: float = 1.0,
                arc_color: str = '#2c55cc',
                arc_width: float = 1.0,
                draw_triangles: bool = True):
    """Draw gnomons and the connected smaller apex arcs (minor arcs)."""
    for g in gnomons:
        if draw_triangles:
            ax.plot([g.base0.real, g.base1.real], [g.base0.imag, g.base1.imag],
                    color=line_color, lw=line_width)
            ax.plot([g.apex.real, g.base0.real], [g.apex.imag, g.base0.imag],
                    color=line_color, lw=line_width)
            ax.plot([g.apex.real, g.base1.real], [g.apex.imag, g.base1.imag],
                    color=line_color, lw=line_width)

        ang0 = angle_deg(g.apex, g.base0)
        ang1 = angle_deg(g.apex, g.base1)
        theta1, theta2 = minor_arc(ang0, ang1)  # choose the smaller arc segment (always CCW)

        arc = Arc((g.apex.real, g.apex.imag),
                  width=2 * g.side_len, height=2 * g.side_len, angle=0.0,
                  theta1=theta1, theta2=theta2,
                  color=arc_color, lw=arc_width)
        ax.add_patch(arc)


def main():
    ap = argparse.ArgumentParser(description='Golden gnomon spiral with connected minor arcs')
    ap.add_argument('--steps', type=int, default=8, help='Number of gnomons / arcs')
    ap.add_argument('--base', type=float, default=1.0, help='Initial base length (long side) of the first gnomon')
    ap.add_argument('--ccw', action='store_true', help='Grow counter-clockwise (default is clockwise)')
    ap.add_argument('--no-triangles', action='store_true', help='Hide gnomon outlines, draw arcs only')
    ap.add_argument('--save', type=str, default='', help='Path to save PNG (optional)')
    ap.add_argument('--show', action='store_true', help='Display the figure in a window')
    ap.add_argument('--pad', type=float, default=0.08, help='Padding around drawing as fraction of initial base')
    args = ap.parse_args()

    gnomons = build_gnomon_chain(steps=args.steps, base_len=args.base, ccw=args.ccw)

    fig, ax = plt.subplots(figsize=(6, 8))
    draw_spiral(
        ax, gnomons,
        line_color='k', line_width=1.0,
        arc_color='#2c55cc', arc_width=1.0,
        draw_triangles=not args.no_triangles
    )
    ax.set_aspect('equal', adjustable='datalim')

    # Bounds from all vertices
    all_pts = [p for g in gnomons for p in (g.apex, g.base0, g.base1)]
    xs = [p.real for p in all_pts]
    ys = [p.imag for p in all_pts]
    pad = args.pad * args.base
    ax.set_xlim(min(xs) - pad, max(xs) + pad)
    ax.set_ylim(min(ys) - pad, max(ys) + pad)
    ax.axis('off')

    if args.save:
        fig.savefig(args.save, dpi=220, bbox_inches='tight', facecolor='white')
    if args.show or not args.save:
        plt.show()


if __name__ == '__main__':
    main()
