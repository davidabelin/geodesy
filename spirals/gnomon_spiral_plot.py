import math
import argparse
from dataclasses import dataclass
from typing import Tuple, List
import cmath
import matplotlib.pyplot as plt
from matplotlib.patches import Arc

Phi = (1 + 5 ** 0.5) / 2

@dataclass
class Tri:
    A: complex
    B: complex
    C: complex


def isosceles_triangle_by_apex(base: float = 1.0, apex_angle_deg: float = 36.0) -> Tri:
    # Base from (0,0) to (base,0). Apex above midpoint.
    b = base
    a2 = math.radians(apex_angle_deg) / 2.0
    h = (b / 2.0) / math.tan(a2)
    A = complex(0.0, 0.0)
    B = complex(b, 0.0)
    C = complex(b / 2.0, h)
    return Tri(A, B, C)


def similarity_about_B_map_A_to_C(tri: Tri):
    # Return similarity F(z) = B + q*(z-B) that maps C -> A (shrinks & rotates inside around B).
    # Choose q = (A-B)/(C-B).
    A, B, C = tri.A, tri.B, tri.C
    q = (A - B) / (C - B)
    def F(z: complex) -> complex:
        return B + q * (z - B)
    return F, q


def circumcircle(p1: complex, p2: complex, p3: complex) -> Tuple[complex, float]:
    # Robust circumcenter via perpendicular bisector intersection formula
    x1, y1 = p1.real, p1.imag
    x2, y2 = p2.real, p2.imag
    x3, y3 = p3.real, p3.imag
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        # Collinear; return large center to avoid crash
        return complex(float('inf'), float('inf')), float('inf')
    x1s = x1 * x1 + y1 * y1
    x2s = x2 * x2 + y2 * y2
    x3s = x3 * x3 + y3 * y3
    ux = (x1s * (y2 - y3) + x2s * (y3 - y1) + x3s * (y1 - y2)) / d
    uy = (x1s * (x3 - x2) + x2s * (x1 - x3) + x3s * (x2 - x1)) / d
    O = complex(ux, uy)
    R = abs(p1 - O)
    return O, R


def angle_deg(center: complex, p: complex) -> float:
    v = p - center
    return math.degrees(math.atan2(v.imag, v.real))


def angle_in_ccw_span(a_start: float, a_end: float, a_test: float) -> bool:
    # Normalize to [0,360)
    def norm(a):
        a = a % 360.0
        if a < 0: a += 360.0
        return a
    s = norm(a_start)
    e = norm(a_end)
    t = norm(a_test)
    span = (e - s) % 360.0
    dt = (t - s) % 360.0
    return dt <= span + 1e-9


def draw_gnomon_spiral(ax, tri: Tri, steps: int = 6, 
                        line_color='k', line_width=1.0, arc_color='#2c55cc', arc_width=1.0):
    # Precompute similarity and sequence of points
    F, q = similarity_about_B_map_A_to_C(tri)
    # Generate sequences A_n = F^n(A), C_n = F^n(C)
    A_seq: List[complex] = [tri.A]
    C_seq: List[complex] = [tri.C]
    for n in range(1, steps + 1):
        A_seq.append(F(A_seq[-1]))
        C_seq.append(F(C_seq[-1]))

    # Draw triangle edges for each step (thin)
    for n in range(0, steps + 1):
        if n == 0:
            A, B, C = tri.A, tri.B, tri.C
        else:
            A, B, C = A_seq[n], tri.B, C_seq[n]
        ax.plot([A.real, B.real], [A.imag, B.imag], color=line_color, lw=line_width)
        ax.plot([B.real, C.real], [B.imag, C.imag], color=line_color, lw=line_width)
        ax.plot([C.real, A.real], [C.imag, A.imag], color=line_color, lw=line_width)

    # Draw circumcircle arcs that pass through apex
    for n in range(0, steps + 1):
        if n == 0:
            A, B, C = tri.A, tri.B, tri.C
            base1, base2, apex = A, B, C
        else:
            A, B, C = A_seq[n], tri.B, C_seq[n]  # apex is C
            base1, base2, apex = B, A, C  # base endpoints
        O, R = circumcircle(A, B, C)
        if math.isinf(R):
            continue
        a1 = angle_deg(O, base1)
        a2 = angle_deg(O, base2)
        aa = angle_deg(O, apex)
        # Ensure we choose the CCW arc from a1->a2 that contains the apex angle 'aa'
        if angle_in_ccw_span(a1, a2, aa):
            theta1, theta2 = a1, a2
        else:
            theta1, theta2 = a2, a1
        arc = Arc((O.real, O.imag), width=2*R, height=2*R, angle=0.0,
                  theta1=theta1, theta2=theta2, color=arc_color, lw=arc_width)
        ax.add_patch(arc)


def main():
    ap = argparse.ArgumentParser(description='Golden-triangle gnomon spiral plotter')
    ap.add_argument('--steps', type=int, default=6, help='Number of nested triangles/arcs')
    ap.add_argument('--base', type=float, default=1.0, help='Base length of outer isosceles triangle')
    ap.add_argument('--apex', type=float, default=36.0, help='Apex angle (deg). 36 forms golden triangle.')
    ap.add_argument('--save', type=str, default='', help='Path to save PNG (optional)')
    ap.add_argument('--show', action='store_true', help='Display the figure in a window')
    ap.add_argument('--pad', type=float, default=0.05, help='Padding around drawing, fraction of base')
    args = ap.parse_args()

    tri = isosceles_triangle_by_apex(base=args.base, apex_angle_deg=args.apex)
    fig, ax = plt.subplots(figsize=(6, 9))
    draw_gnomon_spiral(ax, tri, steps=args.steps, line_width=1.0, arc_width=1.0)
    ax.set_aspect('equal', adjustable='datalim')
    # Set limits
    b = args.base
    pad = args.pad * b
    ax.set_xlim(-pad, b + pad)
    ax.set_ylim(-pad, tri.C.imag + b + pad)
    ax.axis('off')

    if args.save:
        fig.savefig(args.save, dpi=200, bbox_inches='tight', facecolor='white')
    if args.show or not args.save:
        plt.show()

if __name__ == '__main__':
    main()
