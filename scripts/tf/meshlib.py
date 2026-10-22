"""bmesh constructors shared by the turbofan build."""

from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Matrix, Vector


def smooth_mesh(me, angle=math.radians(48)):
    """Shade smooth, then mark edges sharper than `angle` as hard."""
    try:
        me.shade_smooth()
    except Exception:
        for poly in me.polygons:
            poly.use_smooth = True
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.normal_update()
    for edge in bm.edges:
        if len(edge.link_faces) == 2 and edge.calc_face_angle(0.0) > angle:
            edge.smooth = False
    bm.to_mesh(me)
    bm.free()
    me.update()


def _set_uvs(me, uv_by_vert):
    if not uv_by_vert or not me.loops:
        return
    layer = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        for loop_idx in poly.loop_indices:
            vi = me.loops[loop_idx].vertex_index
            if vi < len(uv_by_vert):
                layer.data[loop_idx].uv = uv_by_vert[vi]


def signed_volume(me):
    acc = 0.0
    verts = me.vertices
    for poly in me.polygons:
        if len(poly.vertices) < 3:
            continue
        p0 = verts[poly.vertices[0]].co
        for i in range(1, len(poly.vertices) - 1):
            a = verts[poly.vertices[i]].co
            b = verts[poly.vertices[i + 1]].co
            acc += p0.dot(a.cross(b))
    return acc / 6.0


def flip_normals(me):
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    me.update()


def orient_outward(me):
    if signed_volume(me) < 0.0:
        flip_normals(me)


def _new_object(name, me):
    me.validate(verbose=False)
    me.update()
    return bpy.data.objects.new(name, me)


def spin_shell(name, profiles, angles, wrap=False, closed_profile=True, cap=False, cap_material=1):
    """Revolve per-angle meridional profiles about the engine axis (X).

    profiles: sequence of sequences of (x, r), identical length, r >= 0.
    angles: radians, 0 at +Z, positive toward +Y. Strictly increasing.
    wrap: connect the last angle back to the first (full 360, no end caps).
    cap: fill the first and last profile loops (partial revolution of a closed profile).
    """
    n_ang = len(angles)
    n = len(profiles[0])
    if closed_profile:
        p0, p1 = profiles[0][0], profiles[0][-1]
        if abs(p0[0] - p1[0]) < 1e-9 and abs(p0[1] - p1[1]) < 1e-9:
            profiles = [list(p[:-1]) for p in profiles]
            n = len(profiles[0])

    # Arc-length parameter along the first profile, used as V.
    cum = [0.0]
    base = profiles[0]
    for i in range(1, n):
        dx = base[i][0] - base[i - 1][0]
        dr = base[i][1] - base[i - 1][1]
        cum.append(cum[-1] + math.hypot(dx, dr))
    if closed_profile:
        dx = base[0][0] - base[-1][0]
        dr = base[0][1] - base[-1][1]
        total = cum[-1] + math.hypot(dx, dr)
    else:
        total = cum[-1] or 1.0
    total = total or 1.0

    verts = []
    uvs = []
    for ia, ang in enumerate(angles):
        sa, ca = math.sin(ang), math.cos(ang)
        if wrap:
            u = ia / n_ang
        else:
            u = ia / max(1, n_ang - 1)
        prof = profiles[ia]
        for ip in range(n):
            x, r = prof[ip]
            r = max(0.0, r)
            verts.append((x, r * sa, r * ca))
            uvs.append((u, cum[ip] / total))

    faces = []

    def vid(ia, ip):
        return ia * n + ip

    steps = n_ang if wrap else n_ang - 1
    p_steps = n if closed_profile else n - 1
    for ia in range(steps):
        ia2 = (ia + 1) % n_ang
        for ip in range(p_steps):
            ip2 = (ip + 1) % n
            a = vid(ia, ip)
            b = vid(ia2, ip)
            c = vid(ia2, ip2)
            d = vid(ia, ip2)
            faces.append((a, b, c, d))

    me = bpy.data.meshes.new(name + "_mesh")
    me.from_pydata(verts, [], faces)
    me.update()

    if cap and not wrap and closed_profile:
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

        def boundary_loop(ia):
            edges = []
            for ip in range(n):
                v1 = bm.verts[vid(ia, ip)]
                v2 = bm.verts[vid(ia, (ip + 1) % n)]
                edge = bm.edges.get((v1, v2))
                if edge is not None and edge.is_boundary:
                    edges.append(edge)
            return edges

        for ia in (0, n_ang - 1):
            edges = boundary_loop(ia)
            if len(edges) < 3:
                continue
            filled = bmesh.ops.holes_fill(bm, edges=edges, sides=0)
            faces_new = list(filled.get("faces", []))
            if not faces_new:
                ang = angles[ia]
                normal = Vector((0.0, math.cos(ang), -math.sin(ang)))
                if ia == 0:
                    normal.negate()
                filled = bmesh.ops.triangle_fill(bm, edges=edges, normal=normal, use_beauty=True)
                faces_new = [g for g in filled.get("geom", []) if isinstance(g, bmesh.types.BMFace)]
            for face in faces_new:
                face.material_index = cap_material
        bm.to_mesh(me)
        bm.free()

    _set_uvs(me, uvs)
    if closed_profile and not wrap and cap:
        orient_outward(me)
    elif closed_profile and wrap:
        orient_outward(me)
    smooth_mesh(me)
    return _new_object(name, me)


def revolve(name, profile, segments, a0=0.0, a1=math.tau, cap=False, cap_material=1):
    """Revolve one meridional profile. a1-a0 == tau produces a closed full turn."""
    span = a1 - a0
    full = abs(abs(span) - math.tau) < 1e-4
    if full:
        angles = [a0 + math.tau * i / segments for i in range(segments)]
        profiles = [profile for _ in angles]
        return spin_shell(name, profiles, angles, wrap=True, closed_profile=True, cap=False, cap_material=cap_material)
    angles = [a0 + span * i / segments for i in range(segments + 1)]
    profiles = [profile for _ in angles]
    return spin_shell(
        name, profiles, angles, wrap=False, closed_profile=True, cap=cap, cap_material=cap_material
    )


def loft_sections(name, sections, cap_ends=True, cap_material=0):
    """Loft equal-length closed polylines. sections are lists of (x, y, z)."""
    n_s = len(sections)
    n = len(sections[0])
    verts = []
    uvs = []
    for si, sec in enumerate(sections):
        v = si / max(1, n_s - 1)
        for pi, p in enumerate(sec):
            verts.append(tuple(p))
            uvs.append((pi / n, v))
    faces = []
    for si in range(n_s - 1):
        for pi in range(n):
            a = si * n + pi
            b = si * n + (pi + 1) % n
            c = (si + 1) * n + (pi + 1) % n
            d = (si + 1) * n + pi
            faces.append((a, b, c, d))
    if cap_ends:
        faces.append(tuple(range(n)))
        tip = tuple(range((n_s - 1) * n, n_s * n))
        faces.append(tuple(reversed(tip)))
    me = bpy.data.meshes.new(name + "_mesh")
    me.from_pydata(verts, [], faces)
    me.update()
    _set_uvs(me, uvs)
    orient_outward(me)
    smooth_mesh(me)
    obj = _new_object(name, me)
    if cap_material and me.polygons:
        # Root and tip ngons are the last two faces if caps were requested.
        if cap_ends and cap_material != 0:
            me.polygons[-1].material_index = cap_material
            me.polygons[-2].material_index = cap_material
    return obj


def cylinder_between(p0, p1, radius, segments=10):
    """Return verts, faces for a capped cylinder from p0 to p1. Vert origin is world."""
    p0 = Vector(p0)
    p1 = Vector(p1)
    axis = p1 - p0
    length = axis.length
    if length < 1e-8:
        return [], []
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=segments,
        radius1=radius,
        radius2=radius,
        depth=length,
    )
    direction = axis.normalized()
    quat = direction.to_track_quat("Z", "Y")
    rot = quat.to_matrix().to_4x4()
    mid = (p0 + p1) * 0.5
    mat = Matrix.Translation(mid) @ rot
    bmesh.ops.transform(bm, verts=list(bm.verts), matrix=mat)
    verts = [tuple(v.co) for v in bm.verts]
    faces = [tuple(v.index for v in f.verts) for f in bm.faces]
    bm.free()
    return verts, faces


def mesh_from_pydata(name, verts, faces, smooth_angle=math.radians(48)):
    me = bpy.data.meshes.new(name + "_mesh")
    me.from_pydata(verts, [], faces)
    me.update()
    smooth_mesh(me, smooth_angle)
    orient_outward(me)
    return _new_object(name, me)


def join_meshes(name, objects):
    """Join mesh objects into one new object. Source objects are unlinked by the caller."""
    bm = bmesh.new()
    for obj in objects:
        src = bmesh.new()
        src.from_mesh(obj.data)
        src.transform(obj.matrix_world)
        vert_map = {}
        for v in src.verts:
            vert_map[v.index] = bm.verts.new(v.co)
        bm.verts.ensure_lookup_table()
        for f in src.faces:
            try:
                bm.faces.new(tuple(vert_map[v.index] for v in f.verts))
            except ValueError:
                pass
        src.free()
    me = bpy.data.meshes.new(name + "_mesh")
    bm.to_mesh(me)
    bm.free()
    smooth_mesh(me)
    return _new_object(name, me)


def offset_polyline(pts, distance):
    """Offset a 2D (x, r) polyline by `distance` along its left normal."""
    n = len(pts)
    out = []
    for i, (x, r) in enumerate(pts):
        if i == 0:
            tx, tr = pts[1][0] - x, pts[1][1] - r
        elif i == n - 1:
            tx, tr = x - pts[i - 1][0], r - pts[i - 1][1]
        else:
            tx, tr = pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]
        length = math.hypot(tx, tr) or 1.0
        tx, tr = tx / length, tr / length
        nx, nr = -tr, tx
        out.append((x + nx * distance, max(0.0, r + nr * distance)))
    return out


def catmull_rom(points, samples_per_seg=6):
    if len(points) < 2:
        return list(points)
    padded = [points[0]] + list(points) + [points[-1]]
    out = []
    for i in range(1, len(padded) - 2):
        p0, p1, p2, p3 = padded[i - 1], padded[i], padded[i + 1], padded[i + 2]
        steps = samples_per_seg if i < len(padded) - 3 else samples_per_seg + 1
        for s in range(steps):
            t = s / samples_per_seg
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (
                (2 * p1[0])
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            r = 0.5 * (
                (2 * p1[1])
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            out.append((x, r))
    return out


def closed_strip(centerline, thickness):
    """Closed meridional profile: a sheet thickened equally to either side."""
    # Pick the offset sign so the "outer" side increases radius on the fattest station.
    plus = offset_polyline(centerline, thickness * 0.5)
    minus = offset_polyline(centerline, -thickness * 0.5)
    k = max(range(len(centerline)), key=lambda i: centerline[i][1])
    if plus[k][1] < minus[k][1]:
        plus, minus = minus, plus
    return list(plus) + list(reversed(minus))


def rect_profile(x0, x1, r0, r1):
    """Closed rectangle. r0 is the inner radius, r1 the outer radius."""
    r0, r1 = min(r0, r1), max(r0, r1)
    return [(x0, r0), (x1, r0), (x1, r1), (x0, r1)]


def tapered_shell(x_r_pairs, thickness, outward=True):
    """Closed profile following a wall line (x, r_mid), thickened radially."""
    sign = 1.0 if outward else -1.0
    outer = [(x, max(0.0, r + sign * thickness)) for x, r in x_r_pairs]
    inner = [(x, max(0.0, r)) for x, r in x_r_pairs]
    if not outward:
        outer = [(x, max(0.0, r)) for x, r in x_r_pairs]
        inner = [(x, max(0.0, r - thickness)) for x, r in x_r_pairs]
    return list(outer) + list(reversed(inner))


def sample_wall(xs, radius_fn, thickness, outward=True):
    return tapered_shell([(x, radius_fn(x)) for x in xs], thickness, outward=outward)


def linspace(a, b, n):
    if n <= 1:
        return [a]
    return [a + (b - a) * i / (n - 1) for i in range(n)]
