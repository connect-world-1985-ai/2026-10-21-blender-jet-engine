"""NACA 65-series thickness with a 6-series (a=0.5) mean line.

Thickness ordinates are the public-domain NACA 65-010 table (Abbott and von
Doenhoff / NACA Report 824, recomputed form used by PDAS). The leading edge is
a circular arc at the published 65-010 leading-edge radius, scaled with the
square of thickness ratio, then blended into the table. The mean line is the
analytic six-series line from Abbott and von Doenhoff Eqs. 4-26 and 4-27
(same formulation as the public-domain NASA/PDAS naca456 MeanLine6 routine).
"""

from __future__ import annotations

import math

# NACA 65-010 half-thickness: x in fraction of chord, y in fraction of chord.
# Source table is percent of chord; converted here. Max thickness is 10% at 40% chord.
# Leading-edge radius of the 10% section is 0.624% of chord.
_NACA_65_010 = [
    (0.0000, 0.000000),
    (0.0050, 0.007697),
    (0.0075, 0.009278),
    (0.0125, 0.011616),
    (0.0250, 0.015748),
    (0.0500, 0.021794),
    (0.0750, 0.026491),
    (0.1000, 0.030418),
    (0.1500, 0.036681),
    (0.2000, 0.041435),
    (0.2500, 0.045020),
    (0.3000, 0.047605),
    (0.3500, 0.049247),
    (0.4000, 0.049981),
    (0.4500, 0.049631),
    (0.5000, 0.048089),
    (0.5500, 0.045309),
    (0.6000, 0.041471),
    (0.6500, 0.036823),
    (0.7000, 0.031537),
    (0.7500, 0.025827),
    (0.8000, 0.019851),
    (0.8500, 0.013834),
    (0.9000, 0.008064),
    (0.9500, 0.003010),
    (1.0000, 0.000000),
]

_LE_RADIUS_10 = 0.00624


def _natural_cubic_spline(xs, ys):
    n = len(xs)
    a = list(ys)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    alpha = [0.0] * n
    for i in range(1, n - 1):
        alpha[i] = (3 / h[i]) * (a[i + 1] - a[i]) - (3 / h[i - 1]) * (a[i] - a[i - 1])
    l = [1.0] + [0.0] * (n - 1)
    mu = [0.0] * n
    z = [0.0] * n
    for i in range(1, n - 1):
        l[i] = 2 * (xs[i + 1] - xs[i - 1]) - h[i - 1] * mu[i - 1]
        mu[i] = h[i] / l[i]
        z[i] = (alpha[i] - h[i - 1] * z[i - 1]) / l[i]
    l[-1] = 1.0
    c = [0.0] * n
    b = [0.0] * (n - 1)
    d = [0.0] * (n - 1)
    for j in range(n - 2, -1, -1):
        c[j] = z[j] - mu[j] * c[j + 1]
        b[j] = (a[j + 1] - a[j]) / h[j] - h[j] * (c[j + 1] + 2 * c[j]) / 3
        d[j] = (c[j + 1] - c[j]) / (3 * h[j])

    def evaluate(x):
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        lo, hi = 0, n - 2
        while lo < hi:
            mid = (lo + hi) // 2
            if xs[mid + 1] < x:
                lo = mid + 1
            else:
                hi = mid
        i = lo
        dx = x - xs[i]
        return a[i] + b[i] * dx + c[i] * dx * dx + d[i] * dx * dx * dx

    return evaluate


_SPLINE_65 = _natural_cubic_spline([p[0] for p in _NACA_65_010], [p[1] for p in _NACA_65_010])


def thickness_65(x, toc):
    """Half-thickness fraction of chord for a NACA 65-series section of thickness toc."""
    x = min(1.0, max(0.0, x))
    if x <= 0.0 or x >= 1.0:
        return 0.0
    scale = toc / 0.10
    y_tab = max(0.0, _SPLINE_65(x) * scale)
    rho = _LE_RADIUS_10 * (scale ** 2)
    y_circ = math.sqrt(max(0.0, 2.0 * rho * x - x * x))
    # Hand off from the analytic nose circle to the tabulated 65-series body.
    w = min(1.0, max(0.0, (x - 0.0020) / 0.008))
    w = w * w * (3.0 - 2.0 * w)
    return (1.0 - w) * y_circ + w * y_tab


def mean_line_6(x, a, cl):
    """Return (y, dy/dx) of a NACA 6-series mean line. x, a, y are fractions of chord."""
    if x <= 1e-8 or x >= 1.0 - 1e-8 or abs(cl) < 1e-8:
        return 0.0, 0.0
    pi = math.pi
    oma = 1.0 - a
    xx = min(1.0 - 1e-8, max(1e-8, x))
    omx = 1.0 - xx
    if abs(oma) < 1e-7:
        ym = -(omx * math.log(omx) + xx * math.log(xx)) * cl * (0.25 / pi)
        ymp = (math.log(omx) - math.log(xx)) * cl * (0.25 / pi)
        return ym, ymp
    if abs(a) < 1e-7:
        g = -0.25
        h = -0.5
    else:
        g = -(a * a * (0.5 * math.log(a) - 0.25) + 0.25) / oma
        h = g + (0.5 * oma * oma * math.log(oma) - 0.25 * oma * oma) / oma
    amx = a - xx
    if abs(amx) < 1e-7:
        term1 = 0.0
        term1p = 0.0
    else:
        term1 = amx * amx * (2.0 * math.log(abs(amx)) - 1.0)
        term1p = -amx * math.log(abs(amx))
    term2 = omx * omx * (1.0 - 2.0 * math.log(omx))
    term2p = omx * math.log(omx)
    ym = 0.25 * (term1 + term2) / oma - xx * math.log(xx) + g - h * xx
    ymp = (term1p + term2p) / oma - 1.0 - math.log(xx) - h
    scale = cl / (2.0 * pi * (a + 1.0))
    return ym * scale, ymp * scale


def airfoil_loop(n, toc, cl, a=0.5):
    """Closed section polyline, trailing edge -> upper surface -> leading edge -> lower -> trailing edge.

    Coordinates are fraction of chord, origin at the leading edge, x downstream, y toward the camber side.
    The returned loop does not repeat the first point.
    """
    n = max(8, int(n))
    upper = []
    lower = []
    for i in range(n):
        # Cosine spacing, clustered at the leading and trailing edges.
        x = 0.5 * (1.0 - math.cos(math.pi * i / (n - 1)))
        yth = thickness_65(x, toc)
        ym, yp = mean_line_6(x, a, cl)
        th = math.atan(yp)
        s, c = math.sin(th), math.cos(th)
        upper.append((x - yth * s, ym + yth * c))
        lower.append((x + yth * s, ym - yth * c))
    loop = list(reversed(upper)) + lower[1:-1]
    return loop


def section_stack(loop, chord, stagger_rad):
    """Scale to chord and rotate about the section centroid. Returns (axial, tangential) pairs."""
    pts = [(p[0] * chord, p[1] * chord) for p in loop]
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    cs, sn = math.cos(stagger_rad), math.sin(stagger_rad)
    out = []
    for x, y in pts:
        x -= cx
        y -= cy
        out.append((x * cs - y * sn, x * sn + y * cs))
    return out


def resample_loop(loop, count):
    """Arc-length resample of a closed polyline to exactly `count` points."""
    pts = list(loop)
    n = len(pts)
    segl = []
    total = 0.0
    for i in range(n):
        j = (i + 1) % n
        length = math.hypot(pts[j][0] - pts[i][0], pts[j][1] - pts[i][1])
        segl.append(length)
        total += length
    if total < 1e-12:
        return [pts[0] for _ in range(count)]
    out = []
    for k in range(count):
        target = total * k / count
        acc = 0.0
        placed = False
        for i, length in enumerate(segl):
            if acc + length >= target - 1e-12:
                t = 0.0 if length <= 1e-12 else (target - acc) / length
                p = pts[i]
                q = pts[(i + 1) % n]
                out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
                placed = True
                break
            acc += length
        if not placed:
            out.append(pts[0])
    return out


def align_section(loop):
    """Start at the trailing edge and walk the upper (positive-y) side first.

    Lofting root rectangles into airfoils twists if the point indices disagree.
    """
    pts = list(loop)
    i0 = max(range(len(pts)), key=lambda i: (pts[i][0], pts[i][1]))
    pts = pts[i0:] + pts[:i0]
    n = len(pts)
    quarter = max(2, n // 5)
    y_forward = sum(pts[i][1] for i in range(1, quarter))
    y_back = sum(pts[-i][1] for i in range(1, quarter))
    if y_forward < y_back:
        pts = [pts[0]] + list(reversed(pts[1:]))
    return pts


def rounded_rect_loop(half_axial, half_tang, corner_n=3):
    """Centered rounded rectangle in the (axial, tangential) plane, for blade roots."""
    ha = max(1e-4, half_axial)
    ht = max(1e-4, half_tang)
    r = min(ha, ht) * 0.35
    pts = []

    def arc(cx, cy, a0, a1):
        for i in range(corner_n):
            t = a0 + (a1 - a0) * i / corner_n
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))

    # Start at the trailing-edge side (+axial), go around counterclockwise.
    arc(ha - r, ht - r, 0.0, 0.5 * math.pi)
    arc(-ha + r, ht - r, 0.5 * math.pi, math.pi)
    arc(-ha + r, -ht + r, math.pi, 1.5 * math.pi)
    arc(ha - r, -ht + r, 1.5 * math.pi, 2.0 * math.pi)
    return pts
