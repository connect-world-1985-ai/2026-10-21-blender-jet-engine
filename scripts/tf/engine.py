"""Assemble the turbofan and save the .blend file."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import bpy

from . import meshlib, params
from .airfoil import airfoil_loop, resample_loop, rounded_rect_loop, section_stack
from .materials import build_materials

ROOT = Path(__file__).resolve().parents[2]
BLEND_DIR = ROOT / "2026-10-21-blend"
BLEND_PATH = BLEND_DIR / "turbofan.blend"
HDR_PATH = BLEND_DIR / "hdri" / "studio_small_09_2k.hdr"
TEX_DIR = BLEND_DIR / "textures"


def wrap_pi(angle):
    return (angle + math.pi) % math.tau - math.pi


def in_removed(angle):
    angle = wrap_pi(angle)
    return params.CUT_A0 < angle < params.CUT_A1


def overlaps_removed(alpha, half_width):
    steps = 6
    for i in range(steps + 1):
        if in_removed(alpha - half_width + 2 * half_width * i / steps):
            return True
    return False


def interp(points, x):
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            t = t * t * (3.0 - 2.0 * t)
            return y0 + (y1 - y0) * t
    return points[-1][1]


def core_radii(x):
    """Return (tip, hub) with smooth interpolation along the core gaspath."""
    stations = params.CORE
    if x <= stations[0][0]:
        return stations[0][1], stations[0][2]
    if x >= stations[-1][0]:
        return stations[-1][1], stations[-1][2]
    for i in range(len(stations) - 1):
        x0, t0, h0 = stations[i]
        x1, t1, h1 = stations[i + 1]
        if x0 <= x <= x1:
            u = (x - x0) / (x1 - x0)
            u = u * u * (3.0 - 2.0 * u)
            return t0 + (t1 - t0) * u, h0 + (h1 - h0) * u
    return stations[-1][1], stations[-1][2]


def limit_chord(chord, radius, count, stagger):
    pitch = math.tau * radius / max(1, count)
    limit = 0.80 * pitch / max(0.22, abs(math.sin(stagger)))
    return max(0.012, min(chord, limit))


def seg_for(span, degrees=2.2):
    return max(10, int(round(abs(span) / math.radians(degrees))))


def clip_interval(a0, a1):
    """Pieces of [a0, a1] that lie outside the removed wedge. Angles in radians, a0 < a1, no wrap."""
    cut0, cut1 = params.CUT_A0, params.CUT_A1
    if a1 <= cut0 or a0 >= cut1:
        return [(a0, a1)]
    parts = []
    if a0 < cut0:
        parts.append((a0, min(a1, cut0)))
    if a1 > cut1:
        parts.append((max(a0, cut1), a1))
    return [(s, e) for s, e in parts if e - s > 0.03]


class Builder:
    def __init__(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        BLEND_DIR.mkdir(parents=True, exist_ok=True)
        self.scene = bpy.context.scene
        self.scene.name = "Full"
        self.cut_scene = bpy.data.scenes.new("Cutaway")
        self.mats = build_materials(str(TEX_DIR))
        self._collections()
        self._scatter_groups()
        self.lp = self._empty("Spool_LP")
        self.hp = self._empty("Spool_HP")
        self.common.objects.link(self.lp)
        self.common.objects.link(self.hp)
        self.stats = {"rows": []}
        self.rivet_curves = []

    def _collections(self):
        def col(name):
            return bpy.data.collections.new(name)

        self.studio = col("Studio")
        self.common = col("Common")
        self.full = col("Full")
        self.cut = col("Cut")
        self.glow_full = col("GlowFull")
        self.glow_cut = col("GlowCut")
        self.assets = col("Assets")
        self.assets.hide_render = True
        for c in (self.studio, self.common, self.full, self.glow_full, self.assets):
            self.scene.collection.children.link(c)
        for c in (self.studio, self.common, self.cut, self.glow_cut, self.assets):
            self.cut_scene.collection.children.link(c)

    def _empty(self, name, location=(0.0, 0.0, 0.0)):
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = "PLAIN_AXES"
        obj.empty_display_size = 0.25
        obj.location = location
        return obj

    def _link(self, obj, collection):
        collection.objects.link(obj)
        return obj

    def _assign(self, obj, material, cut_material=None):
        slots = obj.data.materials
        slots.clear()
        slots.append(material)
        if cut_material is not None:
            slots.append(cut_material)
        return obj

    def _parent(self, obj, parent):
        if parent is not None:
            obj.parent = parent
        return obj

    def add_shell(self, name, profile, material, cut_material, span_deg=2.4, parent=None):
        full = meshlib.revolve(name, profile, seg_for(math.tau, span_deg), 0.0, math.tau, cap=False)
        self._assign(full, material, None)
        self._link(full, self.full)
        self._parent(full, parent)
        cut = meshlib.revolve(
            name + "_cut",
            profile,
            seg_for(params.KEEP_A1 - params.KEEP_A0, span_deg),
            params.KEEP_A0,
            params.KEEP_A1,
            cap=True,
        )
        self._assign(cut, material, cut_material)
        self._link(cut, self.cut)
        self._parent(cut, parent)
        return full, cut

    def add_partial(self, name, profile, a0, a1, material, collection, cut_material=None, span_deg=2.2):
        if a1 - a0 < 0.02:
            return None
        obj = meshlib.revolve(name, profile, seg_for(a1 - a0, span_deg), a0, a1, cap=True)
        self._assign(obj, material, cut_material)
        self._link(obj, collection)
        return obj

    def _scatter_group(self, name, source, spacing):
        ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
        ng.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        nodes, links = ng.nodes, ng.links
        gin = nodes.new("NodeGroupInput")
        gout = nodes.new("NodeGroupOutput")
        gin.location = (-800, 0)
        gout.location = (700, 0)
        resample = nodes.new("GeometryNodeResampleCurve")
        resample.location = (-560, 0)
        resample.inputs["Mode"].default_value = "Length"
        resample.inputs["Length"].default_value = spacing
        c2p = nodes.new("GeometryNodeCurveToPoints")
        c2p.location = (-340, 0)
        c2p.mode = "EVALUATED"
        pos = nodes.new("GeometryNodeInputPosition")
        pos.location = (-340, -220)
        sep = nodes.new("ShaderNodeSeparateXYZ")
        sep.location = (-140, -220)
        comb = nodes.new("ShaderNodeCombineXYZ")
        comb.location = (40, -220)
        align = nodes.new("FunctionNodeAlignEulerToVector")
        align.location = (220, -180)
        align.axis = "Z"
        info = nodes.new("GeometryNodeObjectInfo")
        info.location = (-140, -420)
        info.inputs["Object"].default_value = source
        info.inputs["As Instance"].default_value = True
        info.transform_space = "RELATIVE"
        inst = nodes.new("GeometryNodeInstanceOnPoints")
        inst.location = (420, 0)
        realize = nodes.new("GeometryNodeRealizeInstances")
        realize.location = (580, 0)
        links.new(gin.outputs["Geometry"], resample.inputs["Curve"])
        links.new(resample.outputs["Curve"], c2p.inputs["Curve"])
        links.new(pos.outputs["Position"], sep.inputs["Vector"])
        links.new(sep.outputs["Y"], comb.inputs["Y"])
        links.new(sep.outputs["Z"], comb.inputs["Z"])
        links.new(comb.outputs["Vector"], align.inputs["Vector"])
        links.new(c2p.outputs["Points"], inst.inputs["Points"])
        links.new(info.outputs["Geometry"], inst.inputs["Instance"])
        links.new(align.outputs["Rotation"], inst.inputs["Rotation"])
        links.new(inst.outputs["Instances"], realize.inputs["Geometry"])
        links.new(realize.outputs["Geometry"], gout.inputs["Geometry"])
        return ng

    def _scatter_groups(self):
        rivet = self._hardware_mesh("Rivet", 0.0052, 0.0034, 0.0074, self.mats["fastener"], segments=16)
        bolt = self._hardware_mesh("Bolt", 0.0075, 0.006, 0.011, self.mats["steel"], segments=6)
        hole = self._peg_mesh("CoolingHole", 0.0042, 0.009, self.mats["soot"], direction=1.0)
        hole_in = self._peg_mesh("CoolingHoleIn", 0.0042, 0.009, self.mats["soot"], direction=-1.0)
        dil = self._peg_mesh("Dilution", 0.016, 0.018, self.mats["soot"], direction=1.0, segments=10)
        dil_in = self._peg_mesh("DilutionIn", 0.016, 0.018, self.mats["soot"], direction=-1.0, segments=10)
        for obj in (rivet, bolt, hole, hole_in, dil, dil_in):
            obj.hide_render = True
            obj.hide_viewport = True
            self.assets.objects.link(obj)
        self.gn_rivet = self._scatter_group("GN_Rivets", rivet, 0.020)
        self.gn_bolt = self._scatter_group("GN_Bolts", bolt, 0.052)
        self.gn_hole = self._scatter_group("GN_Holes", hole, 0.034)
        self.gn_hole_in = self._scatter_group("GN_HolesIn", hole_in, 0.034)
        self.gn_dil = self._scatter_group("GN_Dilution", dil, 0.20)
        self.gn_dil_in = self._scatter_group("GN_DilutionIn", dil_in, 0.20)

    def _hardware_mesh(self, name, radius, height, head_radius, material, segments=8):
        import bmesh

        bm = bmesh.new()
        bmesh.ops.create_cone(
            bm, cap_ends=True, cap_tris=False, segments=segments,
            radius1=radius, radius2=radius * 0.9, depth=height,
        )
        for vert in bm.verts:
            vert.co.z += height * 0.5
        head = bmesh.new()
        bmesh.ops.create_cone(
            head, cap_ends=True, cap_tris=False, segments=segments,
            radius1=head_radius, radius2=head_radius * 0.82, depth=height * 0.55,
        )
        for vert in head.verts:
            vert.co.z += height + height * 0.55 * 0.5
        vmap = {v: bm.verts.new(v.co) for v in head.verts}
        for face in head.faces:
            try:
                bm.faces.new(tuple(vmap[v] for v in face.verts))
            except ValueError:
                pass
        head.free()
        me = bpy.data.meshes.new(name + "_mesh")
        bm.to_mesh(me)
        bm.free()
        meshlib.smooth_mesh(me, math.radians(35))
        obj = bpy.data.objects.new(name, me)
        self._assign(obj, material)
        return obj

    def _peg_mesh(self, name, radius, length, material, direction=1.0, segments=8):
        import bmesh

        bm = bmesh.new()
        bmesh.ops.create_cone(
            bm, cap_ends=True, cap_tris=False, segments=segments,
            radius1=radius, radius2=radius, depth=length,
        )
        shift = length * 0.5 * direction
        for vert in bm.verts:
            vert.co.z = vert.co.z * direction + shift
        me = bpy.data.meshes.new(name + "_mesh")
        bm.to_mesh(me)
        bm.free()
        obj = bpy.data.objects.new(name, me)
        self._assign(obj, material)
        return obj

    def scatter(self, name, points, group, collection, cyclic=False):
        if len(points) < 2:
            return None
        curve = bpy.data.curves.new(name + "_cu", type="CURVE")
        curve.dimensions = "3D"
        spline = curve.splines.new("POLY")
        spline.points.add(len(points) - 1)
        for point, co in zip(spline.points, points):
            point.co = (co[0], co[1], co[2], 1.0)
        spline.use_cyclic_u = cyclic
        obj = bpy.data.objects.new(name, curve)
        mod = obj.modifiers.new("Scatter", "NODES")
        mod.node_group = group
        self._link(obj, collection)
        self.rivet_curves.append(obj)
        return obj

    def circle_points(self, x, radius, a0, a1, n=None):
        span = a1 - a0
        count = n or max(8, int(abs(span) * radius / 0.04))
        pts = []
        for i in range(count):
            if abs(abs(span) - math.tau) < 1e-3:
                t = a0 + span * i / count
            else:
                t = a0 + span * i / (count - 1)
            pts.append((x, radius * math.sin(t), radius * math.cos(t)))
        return pts

    def radial_line(self, x0, x1, radius_fn, angle, n=18, inset=0.0):
        pts = []
        for i in range(n):
            x = x0 + (x1 - x0) * i / (n - 1)
            radius = radius_fn(x) + inset
            pts.append((x, radius * math.sin(angle), radius * math.cos(angle)))
        return pts

    def add_blade_row(
        self, name, x, r_root, r_tip, count, chord_hub, chord_tip,
        stagger_hub, stagger_tip, cl_hub, cl_tip, toc_hub, toc_tip,
        material, collection_full=True, spool=None, sweep=0.0, lean_deg=0.0,
        n_span=5, n_pts=32, shroud=False, shroud_cut=None, root=None,
    ):
        """Loft one blade and instance it. `root` is a list of (radius, half_ax, half_tang) below the gaspath."""
        sections = []
        # Sections stay in loop order (trailing edge, upper surface, leading edge, lower).
        # Re-sorting after stagger walks the index around the section and twists the loft.
        if root:
            for radius, half_ax, half_tang in root:
                loop = resample_loop(rounded_rect_loop(half_ax, half_tang, corner_n=4), n_pts)
                sections.append(self._place(loop, radius, 0.0, x))
        for i in range(n_span):
            t = i / (n_span - 1)
            radius = r_root + (r_tip - r_root) * t
            stagger = math.radians(stagger_hub + (stagger_tip - stagger_hub) * t)
            bell = math.sin(t * math.pi) ** 0.7
            chord = chord_hub * (1.0 - t) + chord_tip * t + (0.10 * min(chord_hub, chord_tip) + 0.02) * bell
            chord = limit_chord(chord, radius, count, stagger)
            cl = cl_hub + (cl_tip - cl_hub) * t
            toc = max(0.025, toc_hub + (toc_tip - toc_hub) * t)
            loop = resample_loop(airfoil_loop(40, toc, cl), n_pts)
            stacked = section_stack(loop, chord, stagger)
            lean = math.radians(lean_deg) * t
            x_stack = x + sweep * (t ** 1.35)
            sections.append(self._place(stacked, radius, lean, x_stack))
        proto = meshlib.loft_sections(name, sections, cap_ends=True)
        self._assign(proto, material)
        data = proto.data
        bpy.data.objects.remove(proto)
        half = 0.55 * max(chord_hub, chord_tip) / max(r_root, 0.05)
        made_full = made_cut = 0
        for i in range(count):
            alpha = math.tau * i / count
            if collection_full:
                inst = bpy.data.objects.new(f"{name}_{i:03d}", data)
                inst.rotation_euler[0] = -alpha
                self._parent(inst, spool)
                self._link(inst, self.full)
                made_full += 1
            if not overlaps_removed(alpha, half):
                inst = bpy.data.objects.new(f"{name}c_{i:03d}", data)
                inst.rotation_euler[0] = -alpha
                self._parent(inst, spool)
                self._link(inst, self.cut)
                made_cut += 1
        if shroud and shroud_cut is not None:
            axial = max(chord_tip, chord_hub) * 0.55
            profile = meshlib.rect_profile(x - axial * 0.45, x + axial * 0.65, r_tip - 0.001, r_tip + 0.009)
            self.add_shell(name + "_shroud", profile, material, shroud_cut, span_deg=3.0, parent=spool)
            # Knife-edge seals on the shroud outer diameter.
            for k, shift in enumerate((-0.15, 0.25)):
                seal = meshlib.rect_profile(
                    x + shift * axial, x + shift * axial + axial * 0.08,
                    r_tip + 0.008, r_tip + 0.014,
                )
                self.add_shell(f"{name}_seal{k}", seal, self.mats["steel_dark"], shroud_cut, span_deg=4.0, parent=spool)
        self.stats["rows"].append({
            "name": name, "count": count, "x": round(x, 3),
            "r_root": round(r_root, 3), "r_tip": round(r_tip, 3),
            "full": made_full, "cut": made_cut,
        })
        return data

    @staticmethod
    def _place(loop, radius, alpha, x_stack):
        world = []
        radius = max(radius, 0.02)
        for axial, tang in loop:
            angle = alpha + tang / radius
            world.append((x_stack + axial, radius * math.sin(angle), radius * math.cos(angle)))
        return world

    def add_cylinders(self, name, specs, material, collection):
        verts = []
        faces = []
        offset = 0
        for p0, p1, radius, segs in specs:
            v, f = meshlib.cylinder_between(p0, p1, radius, segments=segs)
            if not v:
                continue
            faces.extend(tuple(i + offset for i in face) for face in f)
            verts.extend(v)
            offset += len(v)
        if not verts:
            return None
        obj = meshlib.mesh_from_pydata(name, verts, faces, smooth_angle=math.radians(40))
        self._assign(obj, material)
        self._link(obj, collection)
        return obj

    def wall_between(self, name, x0, x1, radius_fn, thickness, outward, material, cut_material, n=28, parent=None):
        xs = meshlib.linspace(x0, x1, n)
        profile = meshlib.sample_wall(xs, radius_fn, thickness, outward=outward)
        return self.add_shell(name, profile, material, cut_material, parent=parent)

    def build_spools(self):
        m = self.mats
        lp = meshlib.rect_profile(-0.05, 6.35, params.LP_SHAFT_R0, params.LP_SHAFT_R1)
        self.add_shell("LP_Shaft", lp, m["steel"], m["cut_lp"], parent=self.lp)
        hp = meshlib.rect_profile(2.10, 5.28, params.HP_SHAFT_R0, params.HP_SHAFT_R1)
        self.add_shell("HP_Shaft", hp, m["steel"], m["cut_hp"], parent=self.hp)
        # Gaspath walls. Disks sit on the hub; the outer case follows the tip.
        self.wall_between(
            "LPC_Drum", 0.66, 1.72,
            lambda x: core_radii(x)[1] - 0.006,
            0.010, False, m["steel"], m["cut_lp"], n=20, parent=self.lp,
        )
        self.wall_between(
            "HPC_Drum", 2.30, 3.40,
            lambda x: core_radii(x)[1] - 0.006,
            0.010, False, m["steel"], m["cut_hp"], n=20, parent=self.hp,
        )
        self.wall_between(
            "Core_Case", 0.66, 3.38,
            lambda x: core_radii(x)[0] + 0.006,
            0.012, True, m["steel_dark"], m["cut_case"], n=40,
        )
        self.wall_between(
            "Turbine_Case", 4.60, 6.30,
            lambda x: core_radii(x)[0] + 0.010,
            0.014, True, m["temper"], m["cut_hot"], n=32,
        )

    def _disk(self, name, x, r_out, spool, cut_material, half=0.012, r_in=None):
        if r_in is None:
            r_in = params.HP_SHAFT_R1 if spool is self.hp else params.LP_SHAFT_R1
        profile = meshlib.rect_profile(x - half, x + half, r_in, r_out)
        self.add_shell(name, profile, self.mats["steel"], cut_material, span_deg=3.5, parent=spool)

    def build_fan(self):
        m = self.mats
        # Spinner. Solid ogive with a bore so the cut shows a wall, parented to the LP spool.
        outer = []
        x_nose, x_aft = -0.70, 0.20
        for i in range(32):
            t = i / 31.0
            x = x_nose + (x_aft - x_nose) * t
            radius = 0.418 * math.sin(0.5 * math.pi * t) ** 0.80
            outer.append((x, radius))
        spinner = outer + [(x_aft, 0.10), (-0.02, 0.086), (x_nose, 0.0)]
        self.add_shell("Spinner", spinner, m["spinner"], m["cut_lp"], span_deg=2.0, parent=self.lp)
        self._disk("Fan_Disk", 0.08, params.FAN_HUB_R, self.lp, m["cut_lp"], half=0.055, r_in=params.LP_SHAFT_R1)
        root = [
            (0.355, 0.040, 0.022),
            (0.385, 0.034, 0.014),
            (params.FAN_HUB_R, 0.072, 0.040),
        ]
        self.add_blade_row(
            "Fan", x=0.10, r_root=params.FAN_HUB_R + 0.004, r_tip=params.FAN_TIP_R,
            count=params.N_FAN, chord_hub=0.20, chord_tip=0.30,
            stagger_hub=56, stagger_tip=28, cl_hub=0.90, cl_tip=0.22,
            toc_hub=0.095, toc_tip=0.035, material=m["titanium"], spool=self.lp,
            sweep=0.18, lean_deg=7.0, n_span=6, n_pts=42, root=root,
        )
        # Fan case, rub strip, carbon containment belt.
        case = meshlib.rect_profile(-0.02, 0.92, params.CASE_INNER_R, 1.410)
        self.add_shell("Fan_Case", case, m["aluminum"], m["cut_case"], span_deg=2.0)
        rub = meshlib.rect_profile(0.02, 0.42, params.FAN_TIP_R + 0.004, params.CASE_INNER_R)
        self.add_shell("Rubstrip", rub, m["abradable"], m["cut_case"], span_deg=3.0)
        belt = meshlib.rect_profile(0.00, 0.48, 1.408, 1.455)
        self.add_shell("Containment", belt, m["carbon"], m["cut_case"], span_deg=2.5)
        # Outlet guide vanes in the bypass, plus their inner and outer platforms.
        self.add_blade_row(
            "OGV", x=0.84, r_root=0.78, r_tip=1.325,
            count=params.N_OGV, chord_hub=0.30, chord_tip=0.26,
            stagger_hub=16, stagger_tip=8, cl_hub=0.28, cl_tip=0.10,
            toc_hub=0.11, toc_tip=0.07, material=m["steel"], spool=None,
            sweep=0.0, lean_deg=-3.0, n_span=4, n_pts=28,
        )
        inner_band = meshlib.rect_profile(0.70, 1.02, 0.74, 0.785)
        outer_band = meshlib.rect_profile(0.70, 1.02, 1.320, 1.355)
        self.add_shell("OGV_Inner", inner_band, m["steel_dark"], m["cut_case"])
        self.add_shell("OGV_Outer", outer_band, m["steel_dark"], m["cut_case"])

    def _compressor(self, kind, x0, x1, stages, spool, cut_disk, chord0, chord1, stag0, stag1, cl, toc, solidity):
        pitch = (x1 - x0) / stages
        for stage in range(stages):
            base = x0 + pitch * stage
            xr = base + pitch * 0.34
            xs = base + pitch * 0.70
            t = stage / max(1, stages - 1)
            chord = chord0 + (chord1 - chord0) * t
            stagger = stag0 + (stag1 - stag0) * t
            tip, hub = core_radii(xr)
            r_root = hub + 0.004
            r_tip = tip - 0.004
            r_mid = 0.5 * (r_root + r_tip)
            count = int(round(solidity * math.tau * r_mid / chord))
            count = max(18, count - count % 2)
            self._disk(f"{kind}_D{stage}", xr, hub + 0.002, spool, cut_disk, half=0.008)
            self.add_blade_row(
                f"{kind}_R{stage}", xr, r_root, r_tip, count,
                chord * 1.05, chord * 0.82, stagger + 4, stagger - 6,
                cl, cl * 0.65, toc, toc * 0.75,
                self.mats["titanium"], spool=spool, sweep=0.012 * (1 - t), lean_deg=3.0,
                n_span=4, n_pts=28,
            )
            tip_s, hub_s = core_radii(xs)
            r_mid_s = 0.5 * (hub_s + tip_s)
            count_s = int(round((solidity + 0.1) * math.tau * r_mid_s / (chord * 0.9)))
            count_s = max(20, count_s - count_s % 2)
            self.add_blade_row(
                f"{kind}_S{stage}", xs, hub_s + 0.003, tip_s - 0.003, count_s,
                chord * 0.95, chord * 0.75, -(stagger - 6), -(stagger - 14),
                -cl * 0.85, -cl * 0.5, toc * 0.9, toc * 0.7,
                self.mats["steel"], spool=None, n_span=4, n_pts=26,
            )
            if kind == "HPC" and stage < 3:
                self._unison(stage, xs, tip_s, count_s)

    def _unison(self, stage, x, tip, count):
        ring_r = tip + 0.055
        profile = []
        for i in range(14):
            ang = math.tau * i / 14
            profile.append((x + 0.010 * math.cos(ang), ring_r + 0.008 * math.sin(ang)))
        self.add_shell(f"Unison_{stage}", profile, self.mats["steel_dark"], self.mats["cut_hp"], span_deg=4.0)
        specs_full, specs_cut = [], []
        for i in range(count):
            alpha = math.tau * i / count
            p0 = (x, (tip + 0.01) * math.sin(alpha), (tip + 0.01) * math.cos(alpha))
            p1 = (x, (ring_r - 0.006) * math.sin(alpha), (ring_r - 0.006) * math.cos(alpha))
            specs_full.append((p0, p1, 0.0045, 6))
            if not in_removed(alpha):
                specs_cut.append((p0, p1, 0.0045, 6))
        self.add_cylinders(f"VSV_stem_{stage}", specs_full, self.mats["steel"], self.full)
        self.add_cylinders(f"VSV_stem_{stage}_cut", specs_cut, self.mats["steel"], self.cut)

    def build_compressors(self):
        self._compressor(
            "LPC", *params.X_LPC, params.N_LPC, self.lp, self.mats["cut_lp"],
            chord0=0.055, chord1=0.042, stag0=36, stag1=48, cl=0.55, toc=0.085, solidity=1.15,
        )
        self._compressor(
            "HPC", *params.X_HPC, params.N_HPC, self.hp, self.mats["cut_hp"],
            chord0=0.046, chord1=0.030, stag0=40, stag1=54, cl=0.50, toc=0.075, solidity=1.20,
        )

    def build_combustor(self):
        m = self.mats
        x0, x1 = params.X_COMB
        outer = meshlib.rect_profile(3.86, 4.50, 0.575, 0.598)
        inner = meshlib.rect_profile(3.86, 4.50, 0.292, 0.312)
        dome = meshlib.rect_profile(3.72, 3.78, 0.300, 0.590)
        case = meshlib.rect_profile(3.66, 4.60, 0.610, 0.645)
        self.add_shell("Comb_OuterLiner", outer, m["ceramic"], m["cut_hot"], span_deg=2.5)
        self.add_shell("Comb_InnerLiner", inner, m["ceramic"], m["cut_hot"], span_deg=2.5)
        self.add_shell("Comb_Dome", dome, m["ceramic"], m["cut_hot"], span_deg=3.0)
        self.add_shell("Comb_Case", case, m["steel_dark"], m["cut_hot"], span_deg=2.5)
        # Fuel manifold.
        manifold = []
        for i in range(16):
            ang = math.tau * i / 16
            manifold.append((3.74 + 0.016 * math.cos(ang), 0.70 + 0.016 * math.sin(ang)))
        self.add_shell("FuelManifold", manifold, m["steel"], m["cut_hot"], span_deg=3.5)
        self._swirlers()
        self._hole_rows(3.92, 4.42, 0.573, 8, self.gn_hole, "HoleOut", inward_surface=False)
        self._hole_rows(3.92, 4.42, 0.314, 6, self.gn_hole_in, "HoleIn", inward_surface=True)
        self._hole_rows(4.05, 4.35, 0.573, 2, self.gn_dil, "DilOut", inward_surface=False)
        # Igniter on the kept side of the case.
        alpha = math.radians(25)
        p0 = (4.15, 0.64 * math.sin(alpha), 0.64 * math.cos(alpha))
        p1 = (4.15, 0.78 * math.sin(alpha), 0.78 * math.cos(alpha))
        self.add_cylinders("Igniter", [(p0, p1, 0.012, 10)], m["steel_dark"], self.common)
        # Annular flame volume, full and sectioned.
        flame = meshlib.rect_profile(3.95, 4.40, 0.34, 0.52)
        full = meshlib.revolve("Flame_Comb", flame, 48, 0.0, math.tau, cap=False)
        cut = meshlib.revolve("Flame_Comb_cut", flame, 36, params.KEEP_A0, params.KEEP_A1, cap=False)
        for obj, col in ((full, self.glow_full), (cut, self.glow_cut)):
            self._assign(obj, m["flame"])
            self._link(obj, col)

    def _swirlers(self):
        m = self.mats
        cup = meshlib.revolve("SwirlCup", meshlib.rect_profile(0.0, 0.048, 0.022, 0.038), 24, 0.0, math.tau, cap=False)
        hub_profile = [(0.008, 0.0), (0.046, 0.0), (0.030, 0.012)]
        hub = meshlib.revolve("SwirlHub", hub_profile, 16, 0.0, math.tau, cap=False)
        # Eight tiny vanes around the swirler axis. Built at the origin, then joined.
        vane_sections = []
        vane_n = 8
        for i in range(4):
            t = i / 3
            radius = 0.012 + 0.009 * t
            stagger = math.radians(42 - 10 * t)
            chord = limit_chord(0.016, radius, vane_n, stagger)
            loop = resample_loop(airfoil_loop(16, 0.12, 0.4), 20)
            stacked = section_stack(loop, chord, stagger)
            vane_sections.append(self._place(stacked, radius, 0.0, 0.024))
        vane = meshlib.loft_sections("SwirlVane", vane_sections)
        # Duplicate the vane mesh around the local axis by joining rotated copies.
        import bmesh
        from mathutils import Matrix

        bm = bmesh.new()
        for src in (cup, hub, vane):
            tmp = bmesh.new()
            tmp.from_mesh(src.data)
            if src is vane:
                for k in range(vane_n):
                    spun = bmesh.new()
                    spun.from_mesh(src.data)
                    rot = Matrix.Rotation(math.tau * k / vane_n, 4, "X")
                    spun.transform(rot)
                    self._bm_join(bm, spun)
                    spun.free()
            else:
                self._bm_join(bm, tmp)
            tmp.free()
        me = bpy.data.meshes.new("Swirler_mesh")
        bm.to_mesh(me)
        bm.free()
        meshlib.smooth_mesh(me)
        for src in (cup, hub, vane):
            mesh = src.data
            bpy.data.objects.remove(src)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        x = 3.80
        radius = 0.45
        stems_full, stems_cut = [], []
        count = params.N_SWIRLER
        for i in range(count):
            alpha = math.tau * i / count
            y = radius * math.sin(alpha)
            z = radius * math.cos(alpha)
            if True:
                obj = bpy.data.objects.new(f"Swirler_{i:02d}", me)
                obj.location = (x, y, z)
                self._assign(obj, m["steel_dark"])
                self._link(obj, self.full)
            p0 = (x + 0.02, (radius + 0.04) * math.sin(alpha), (radius + 0.04) * math.cos(alpha))
            p1 = (x + 0.02, 0.685 * math.sin(alpha), 0.685 * math.cos(alpha))
            stems_full.append((p0, p1, 0.006, 6))
            if not in_removed(alpha):
                obj = bpy.data.objects.new(f"SwirlerC_{i:02d}", me)
                obj.location = (x, y, z)
                self._assign(obj, m["steel_dark"])
                self._link(obj, self.cut)
                stems_cut.append((p0, p1, 0.006, 6))
        self.add_cylinders("FuelStems", stems_full, m["steel"], self.full)
        self.add_cylinders("FuelStems_cut", stems_cut, m["steel"], self.cut)

    @staticmethod
    def _bm_join(dest, src):
        vmap = {v: dest.verts.new(v.co) for v in src.verts}
        for face in src.faces:
            try:
                dest.faces.new(tuple(vmap[v] for v in face.verts))
            except ValueError:
                pass

    def _hole_rows(self, x0, x1, radius, rows, group, prefix, inward_surface):
        # inward_surface selects the scatter group; radius is where the peg starts.
        for i in range(rows):
            x = x0 + (x1 - x0) * (i + 0.5) / rows
            full_pts = self.circle_points(x, radius, 0.0, math.tau)
            self.scatter(f"{prefix}_{i}", full_pts, group, self.full, cyclic=True)
            cut_pts = self.circle_points(x, radius, params.KEEP_A0, params.KEEP_A1, n=48)
            self.scatter(f"{prefix}c_{i}", cut_pts, group, self.cut, cyclic=False)

    def build_turbines(self):
        m = self.mats
        self._turbine_spool(
            "HPT", *params.X_HPT, params.N_HPT, self.hp, m["cut_hp"],
            chord=0.058, stagger=32, cl=-0.65, toc=0.09, solidity=1.25, shroud=True,
        )
        self._turbine_spool(
            "LPT", *params.X_LPT, params.N_LPT, self.lp, m["cut_lp"],
            chord=0.070, stagger=28, cl=-0.45, toc=0.08, solidity=1.15, shroud=True,
        )
        # Tail cone.
        cone = [(6.20, 0.08), (6.20, 0.30), (6.95, 0.02), (6.20, 0.08)]
        self.add_shell("Tailcone", cone, m["temper"], m["cut_lp"], parent=self.lp, span_deg=3.0)

    def _turbine_spool(self, kind, x0, x1, stages, spool, cut_color, chord, stagger, cl, toc, solidity, shroud):
        pitch = (x1 - x0) / stages
        # Inlet nozzle.
        x_ngv = x0 + pitch * 0.18
        tip, hub = core_radii(x_ngv)
        r_mid = 0.5 * (tip + hub)
        n_ngv = max(16, int(round(solidity * math.tau * r_mid / chord)) // 2 * 2)
        self.add_blade_row(
            f"{kind}_NGV", x_ngv, hub + 0.004, tip - 0.006, n_ngv,
            chord * 1.1, chord * 0.85, -stagger, -(stagger - 8),
            cl, cl * 0.7, toc, toc * 0.8,
            self.mats["temper"], spool=None, n_span=5, n_pts=32,
        )
        band_i = meshlib.rect_profile(x_ngv - chord * 0.45, x_ngv + chord * 0.55, hub - 0.004, hub + 0.008)
        band_o = meshlib.rect_profile(x_ngv - chord * 0.45, x_ngv + chord * 0.55, tip - 0.008, tip + 0.010)
        self.add_shell(f"{kind}_NGVi", band_i, self.mats["temper"], cut_color)
        self.add_shell(f"{kind}_NGVo", band_o, self.mats["temper"], cut_color)
        for stage in range(stages):
            xr = x0 + pitch * (stage + 0.62)
            tip, hub = core_radii(xr)
            r_mid = 0.5 * (tip + hub)
            count = max(18, int(round(solidity * 1.15 * math.tau * r_mid / chord)) // 2 * 2)
            self._disk(f"{kind}_D{stage}", xr, hub, spool, cut_color, half=0.012)
            self.add_blade_row(
                f"{kind}_R{stage}", xr, hub + 0.003, tip - 0.010, count,
                chord, chord * 0.78, stagger + 6, stagger - 4,
                -cl, -cl * 0.6, toc * 0.85, toc * 0.6,
                self.mats["temper"], spool=spool, sweep=0.02, lean_deg=-4.0,
                n_span=5, n_pts=32, shroud=shroud, shroud_cut=cut_color,
            )

    def build_bypass_and_mixer(self):
        m = self.mats
        self.wall_between(
            "Bypass_Outer", 0.40, 6.62, lambda x: interp(params.BYPASS_OUTER, x),
            0.016, True, m["aluminum"], m["cut_case"], n=36,
        )
        self.wall_between(
            "Core_Cowl", 0.52, 6.08, lambda x: interp(params.CORE_COWL, x),
            0.014, True, m["steel_dark"], m["cut_case"], n=36,
        )
        # Lobed forced mixer. Radius rides on the core-cowl line and waves in angle.
        angles_full = [math.tau * i / 96 for i in range(96)]
        self._mixer("Mixer", angles_full, wrap=True, collection=self.full, cap=False)
        n_cut = seg_for(params.KEEP_A1 - params.KEEP_A0, 3.5)
        angles_cut = [
            params.KEEP_A0 + (params.KEEP_A1 - params.KEEP_A0) * i / n_cut for i in range(n_cut + 1)
        ]
        self._mixer("Mixer_cut", angles_cut, wrap=False, collection=self.cut, cap=True)

    def _mixer(self, name, angles, wrap, collection, cap):
        x0, x1 = params.X_MIX
        profiles = []
        lobes = 16
        for angle in angles:
            lobe = math.sin(lobes * angle)
            center = []
            for i in range(18):
                t = i / 17
                x = x0 + (x1 - x0) * t
                env = math.sin(math.pi * t) ** 0.8
                radius = interp(params.CORE_COWL, min(x, params.CORE_COWL[-1][0])) + 0.045 * lobe * env
                center.append((x, max(0.2, radius)))
            profiles.append(meshlib.closed_strip(center, 0.012))
        obj = meshlib.spin_shell(name, profiles, angles, wrap=wrap, closed_profile=True, cap=cap, cap_material=1)
        self._assign(obj, self.mats["steel_dark"], self.mats["cut_case"] if cap else None)
        self._link(obj, collection)

    def build_afterburner(self):
        m = self.mats
        duct = meshlib.rect_profile(6.65, 7.50, 0.98, 1.045)
        liner = meshlib.rect_profile(6.80, 7.45, 0.90, 0.925)
        self.add_shell("AB_Duct", duct, m["afterburner"], m["cut_ab"], span_deg=2.2)
        self.add_shell("AB_Liner", liner, m["afterburner"], m["cut_ab"], span_deg=2.5)
        for index, radius in enumerate((0.55, 0.78)):
            gutter = [(7.05, radius), (7.16, radius + 0.030), (7.16, radius - 0.030)]
            self.add_shell(f"Gutter_{index}", gutter, m["afterburner"], m["cut_ab"], span_deg=3.0)
        # Radial flame-holder spokes and spray bars.
        specs_f, specs_c = [], []
        for i in range(10):
            alpha = math.tau * i / 10 + 0.1
            p0 = (7.10, 0.55 * math.sin(alpha), 0.55 * math.cos(alpha))
            p1 = (7.10, 0.78 * math.sin(alpha), 0.78 * math.cos(alpha))
            specs_f.append((p0, p1, 0.008, 6))
            if not in_removed(alpha):
                specs_c.append((p0, p1, 0.008, 6))
        self.add_cylinders("GutterSpokes", specs_f, m["afterburner"], self.full)
        self.add_cylinders("GutterSpokes_cut", specs_c, m["afterburner"], self.cut)
        bars_f, bars_c = [], []
        for i in range(12):
            alpha = math.tau * i / 12
            p0 = (6.95, 0.40 * math.sin(alpha), 0.40 * math.cos(alpha))
            p1 = (6.95, 0.90 * math.sin(alpha), 0.90 * math.cos(alpha))
            bars_f.append((p0, p1, 0.010, 8))
            if not in_removed(alpha):
                bars_c.append((p0, p1, 0.010, 8))
        self.add_cylinders("Spraybars", bars_f, m["steel_dark"], self.full)
        self.add_cylinders("Spraybars_cut", bars_c, m["steel_dark"], self.cut)
        self._hole_rows(6.90, 7.35, 0.928, 5, self.gn_hole, "ABHole", inward_surface=False)
        # Nozzle petals: convergent inner row, divergent outer row, plus actuators.
        self._petals(
            "Conv", params.X_NOZ_HINGE, params.X_NOZ_THROAT, 0.90, 0.70, 0.012, overlap=1.22,
        )
        self._petals(
            "Div", params.X_NOZ_THROAT - 0.02, params.X_NOZ_EXIT, 0.78, 0.92, 0.014, overlap=1.18,
        )
        self._actuators()
        flame = meshlib.rect_profile(7.00, 7.55, 0.18, 0.52)
        full = meshlib.revolve("Flame_AB", flame, 40, 0.0, math.tau, cap=False)
        cut = meshlib.revolve("Flame_AB_cut", flame, 30, params.KEEP_A0, params.KEEP_A1, cap=False)
        for obj, col in ((full, self.glow_full), (cut, self.glow_cut)):
            self._assign(obj, m["flame"])
            self._link(obj, col)

    def _petals(self, name, x0, x1, r0, r1, thickness, overlap):
        count = params.N_PETALS
        width = math.tau / count * overlap
        for i in range(count):
            center = math.tau * i / count
            a0 = center - width * 0.5
            a1 = center + width * 0.5
            # Keep the petal angle in a continuous range for the revolve.
            profile = [(x0, r0), (x1, r1), (x1, r1 + thickness), (x0, r0 + thickness)]
            self.add_partial(f"{name}_{i:02d}", profile, a0, a1, self.mats["afterburner"], self.full, span_deg=4.0)
            for s, e in clip_interval(wrap_pi(a0) if False else a0, a1):
                # clip_interval expects the petal's own angle range; petals near +/-pi may wrap.
                pass
            kept = self._kept_angles(a0, a1)
            for k, (s, e) in enumerate(kept):
                self.add_partial(
                    f"{name}c_{i:02d}_{k}", profile, s, e,
                    self.mats["afterburner"], self.cut, self.mats["cut_ab"], span_deg=4.5,
                )

    def _kept_angles(self, a0, a1):
        """Intersect a petal interval with the kept 270 degree sweep, allowing wrap."""
        # Sample the interval and merge contiguous kept samples into spans.
        samples = 24
        flags = []
        angles = []
        for i in range(samples + 1):
            a = a0 + (a1 - a0) * i / samples
            angles.append(a)
            flags.append(not in_removed(a))
        spans = []
        start = None
        for a, keep in zip(angles, flags):
            if keep and start is None:
                start = a
            elif not keep and start is not None:
                spans.append((start, a))
                start = None
        if start is not None:
            spans.append((start, angles[-1]))
        return [(s, e) for s, e in spans if e - s > 0.04]

    def _actuators(self):
        specs_f, specs_c = [], []
        count = 8
        for i in range(count):
            alpha = math.tau * (i + 0.5) / count
            p0 = (7.35, 1.08 * math.sin(alpha), 1.08 * math.cos(alpha))
            p1 = (7.95, 0.98 * math.sin(alpha), 0.98 * math.cos(alpha))
            specs_f.append((p0, p1, 0.018, 8))
            # Rod end at the petal.
            p2 = (8.05, 0.95 * math.sin(alpha), 0.95 * math.cos(alpha))
            specs_f.append((p1, p2, 0.012, 6))
            if not in_removed(alpha):
                specs_c.append((p0, p1, 0.018, 8))
                specs_c.append((p1, p2, 0.012, 6))
        self.add_cylinders("Actuators", specs_f, self.mats["steel_dark"], self.full)
        self.add_cylinders("Actuators_cut", specs_c, self.mats["steel_dark"], self.cut)

    def build_nacelle(self):
        m = self.mats
        lip_cl = self._lip_centerline()
        lip = meshlib.closed_strip(lip_cl, 0.016)
        self.add_shell("InletLip", lip, m["aluminum"], m["cut_case"], span_deg=1.8)
        # Dark sleeve under the panel gaps, and the panel bays themselves.
        def r_oml(x):
            return interp(params.OUTER_COWL, x)

        self.wall_between(
            "CowlSleeve", 0.16, 7.08, lambda x: r_oml(x) - 0.030,
            0.008, True, m["gap"], m["cut_case"], n=24,
        )
        bays = [(0.20, 2.08), (2.16, 4.52), (4.60, 7.02)]
        gap = math.radians(0.42)
        width = math.tau / 6.0 - gap
        centers = [math.radians(c) for c in (-90, -30, 30, 90, 150, -150)]
        self.focus_rivet = None
        for bi, (x0, x1) in enumerate(bays):
            xs = meshlib.linspace(x0, x1, 14)
            outer = [(x, r_oml(x)) for x in xs]
            inner = [(x, r_oml(x) - 0.018) for x in xs]
            profile = outer + inner[::-1]
            for pi, center in enumerate(centers):
                a0 = center - width * 0.5
                a1 = center + width * 0.5
                self.add_partial(f"Panel_{bi}_{pi}", profile, a0, a1, m["paint"], self.full, span_deg=2.0)
                for k, (s, e) in enumerate(clip_interval(a0, a1)):
                    self.add_partial(
                        f"Panelc_{bi}_{pi}_{k}", profile, s, e,
                        m["paint"], self.cut, m["cut_case"], span_deg=2.2,
                    )
                    self._panel_rivets(f"{bi}c{k}", pi, x0, x1, s, e, r_oml, self.cut, full_circle=False)
                self._panel_rivets(bi, pi, x0, x1, a0, a1, r_oml, self.full, full_circle=False)
                if self.focus_rivet is None and abs(center - math.radians(-90)) < 0.2 and bi == 0:
                    rivet_a = center + width * 0.5 - 0.014
                    rivet_x = x0 + (x1 - x0) * 0.42
                    rr = r_oml(rivet_x) + 0.006
                    self.focus_rivet = (rivet_x, rr * math.sin(rivet_a), rr * math.cos(rivet_a))
        # Carbon access door on the hero-side fan cowl.
        x0, x1 = 0.55, 1.70
        center = math.radians(-90)
        half_a = math.radians(18)
        xs = meshlib.linspace(x0, x1, 12)
        outer = [(x, r_oml(x) + 0.009) for x in xs]
        inner = [(x, r_oml(x) + 0.0015) for x in xs]
        door = outer + inner[::-1]
        self.add_partial("AccessDoor", door, center - half_a, center + half_a, m["carbon"], self.full, span_deg=2.0)
        self._rect_rivets("DoorRivets", x0 + 0.03, x1 - 0.03, center - half_a + 0.02, center + half_a - 0.02, r_oml, 0.010, self.full)
        # Pylon and gearbox blister live in both scenes.
        self._pylon()
        self._blister(r_oml)
        # Fan-case flange and a core flange, with bolt circles.
        self._flange("Flange_Fan", 0.90, 1.40, m["aluminum"], m["cut_case"])
        self._flange("Flange_HPC", 3.20, 0.46, m["steel"], m["cut_hp"])
        self._flange("Flange_AB", 6.68, 1.05, m["afterburner"], m["cut_ab"])
        # Bleed duct on the kept side, between the HPC case and the core cowl.
        alpha = math.radians(55)
        bleed = []
        for i in range(12):
            t = i / 11
            x = 3.15 + (2.35 - 3.15) * t
            radius = 0.48 + (0.78 - 0.48) * math.sin(t * math.pi * 0.5)
            bleed.append((x, radius * math.sin(alpha), radius * math.cos(alpha)))
        curve = self.scatter("Bleed", bleed, self.gn_bolt, self.common, cyclic=False)
        # The bolt group would sprinkle fasteners along the pipe. Use a real tube instead.
        if curve is not None:
            bpy.data.objects.remove(curve, do_unlink=True)
            self.rivet_curves.remove(curve)
        cu = bpy.data.curves.new("Bleed_cu", type="CURVE")
        cu.dimensions = "3D"
        cu.bevel_depth = 0.018
        cu.bevel_resolution = 4
        sp = cu.splines.new("POLY")
        sp.points.add(len(bleed) - 1)
        for point, co in zip(sp.points, bleed):
            point.co = (co[0], co[1], co[2], 1.0)
        pipe = bpy.data.objects.new("BleedPipe", cu)
        self._assign(pipe, m["steel"])
        self._link(pipe, self.common)

    def _lip_centerline(self):
        arc = []
        n = 28
        for i in range(n):
            t = params.LIP_A0 + (params.LIP_A1 - params.LIP_A0) * i / (n - 1)
            arc.append((
                params.LIP_C[0] + params.LIP_AX * math.cos(t),
                params.LIP_C[1] + params.LIP_BR * math.sin(t),
            ))
        inner = [(0.04, 1.372), (-0.12, 1.366), arc[0]]
        outer = [arc[-1], (-0.02, 1.552), (0.12, 1.548)]
        # Drop duplicated joints.
        cl = inner[:-1] + arc + outer[1:]
        return cl

    def _panel_rivets(self, bi, pi, x0, x1, a0, a1, r_fn, collection, full_circle):
        inset = 0.012
        r0 = r_fn(x0) + 0.001
        r1 = r_fn(x1) + 0.001
        # Two axial rows, inset from the longitudinal edges, and two circumferential rows.
        for sign, edge in ((1, a0 + inset), (-1, a1 - inset)):
            pts = self.radial_line(x0 + 0.02, x1 - 0.02, r_fn, edge, n=16, inset=0.001)
            self.scatter(f"RivA_{bi}_{pi}_{sign}", pts, self.gn_rivet, collection)
        for x, tag in ((x0 + 0.025, "f"), (x1 - 0.025, "a")):
            radius = r_fn(x) + 0.001
            pts = self.circle_points(x, radius, a0 + inset, a1 - inset, n=18)
            self.scatter(f"RivC_{bi}_{pi}_{tag}", pts, self.gn_rivet, collection)

    def _rect_rivets(self, name, x0, x1, a0, a1, r_fn, inset_r, collection):
        for tag, angle in (("a", a0), ("b", a1)):
            pts = self.radial_line(x0, x1, r_fn, angle, n=14, inset=inset_r)
            self.scatter(f"{name}_{tag}", pts, self.gn_rivet, collection)
        for tag, x in (("f", x0), ("r", x1)):
            pts = self.circle_points(x, r_fn(x) + inset_r, a0, a1, n=12)
            self.scatter(f"{name}_{tag}", pts, self.gn_rivet, collection)

    def _flange(self, name, x, radius, material, cut_material):
        profile = meshlib.rect_profile(x, x + 0.018, radius, radius + 0.032)
        self.add_shell(name, profile, material, cut_material, span_deg=3.0)
        bolt_r = radius + 0.020
        self.scatter(name + "_bolts", self.circle_points(x + 0.009, bolt_r, 0.0, math.tau, n=40), self.gn_bolt, self.full, cyclic=True)
        self.scatter(
            name + "_bolts_cut",
            self.circle_points(x + 0.009, bolt_r, params.KEEP_A0, params.KEEP_A1, n=28),
            self.gn_bolt, self.cut, cyclic=False,
        )

    def _pylon(self):
        # Swept, tapered strut. Section is an airfoil in the axial-lateral plane.
        sections = []
        stations = (
            (1.48, 2.35, 0.22, 0.13, 0.20),
            (1.82, 1.85, 0.55, 0.12, 0.25),
            (2.18, 1.40, 0.95, 0.11, 0.30),
            (2.58, 0.95, 1.40, 0.10, 0.35),
        )
        for z, chord, x_le, toc, cl in stations:
            loop = resample_loop(airfoil_loop(22, toc, cl), 32)
            sec = [(x_le + x * chord, y * chord, z) for x, y in loop]
            sections.append(sec)
        obj = meshlib.loft_sections("Pylon", sections)
        self._assign(obj, self.mats["paint"])
        self._link(obj, self.common)

    def _blister(self, r_fn):
        import bmesh
        from mathutils import Matrix

        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=16, radius=1.0)
        x = 1.35
        radius = r_fn(x) - 0.04
        # Bottom of the nacelle, outside the cut wedge.
        scale = Matrix.Diagonal((0.55, 0.34, 0.22, 1.0))
        loc = Matrix.Translation((x, 0.0, -radius))
        bmesh.ops.transform(bm, verts=list(bm.verts), matrix=loc @ scale)
        me = bpy.data.meshes.new("Blister_mesh")
        bm.to_mesh(me)
        bm.free()
        meshlib.smooth_mesh(me)
        obj = bpy.data.objects.new("GearboxFairing", me)
        self._assign(obj, self.mats["paint"])
        self._link(obj, self.common)

    def build_bearings(self):
        m = self.mats
        stations = [
            ("FanBrg", 0.02, 0.095, 0.150, self.lp, m["cut_lp"]),
            ("HPFront", 2.22, 0.155, 0.205, self.hp, m["cut_hp"]),
            ("HPRear", 5.12, 0.155, 0.210, self.hp, m["cut_hp"]),
            ("LPRear", 6.22, 0.095, 0.155, self.lp, m["cut_lp"]),
        ]
        for name, x, r_in, r_out, spool, cut in stations:
            inner = meshlib.rect_profile(x - 0.012, x + 0.012, r_in, r_in + 0.012)
            outer = meshlib.rect_profile(x - 0.014, x + 0.014, r_out - 0.012, r_out)
            self.add_shell(name + "_in", inner, m["steel"], cut, span_deg=4, parent=spool)
            self.add_shell(name + "_out", outer, m["steel_dark"], cut, span_deg=4)
            pitch = 0.5 * (r_in + r_out)
            specs_f, specs_c = [], []
            balls = 14
            for i in range(balls):
                alpha = math.tau * i / balls
                center = (x, pitch * math.sin(alpha), pitch * math.cos(alpha))
                end = (x + 0.001, pitch * math.sin(alpha), pitch * math.cos(alpha))
                specs_f.append((center, end, 0.010, 8))
                if not in_removed(alpha):
                    specs_c.append((center, end, 0.010, 8))
            # Short "cylinders" read as simplified rollers at this scale.
            self.add_cylinders(name + "_rollers", specs_f, m["steel"], self.full)
            self.add_cylinders(name + "_rollers_cut", specs_c, m["steel"], self.cut)

    def build_studio(self):
        m = self.mats
        # Floor sits just below the nacelle.
        floor_z = -1.78
        profile_not_used = None
        import bmesh
        bm = bmesh.new()
        bmesh.ops.create_grid(bm, x_segments=2, y_segments=2, size=18.0)
        # Grid is in XY; rotate to XZ? Engine axis is X, up is Z, so the floor is the XY plane at z=floor.
        # create_grid builds in XY already (z=0). Translate down? Nacelle extends to z=-1.55, floor at -1.78.
        # But our floor should be horizontal: normal +Z, which is the XY plane. create_grid is XY. Good.
        for vert in bm.verts:
            vert.co.z = floor_z
        me = bpy.data.meshes.new("Floor_mesh")
        bm.to_mesh(me)
        bm.free()
        floor = bpy.data.objects.new("Floor", me)
        self._assign(floor, m["floor"])
        self._link(floor, self.studio)
        focus = self._empty("FOCUS_ENGINE", (3.4, 0.0, 0.25))
        self._link(focus, self.studio)
        self.focus = focus
        rivet_loc = self.focus_rivet or (1.3, -1.45, 0.55)
        self.focus_rivet_empty = self._empty("FOCUS_RIVET", rivet_loc)
        self._link(self.focus_rivet_empty, self.studio)
        self.focus_fan = self._empty("FOCUS_FAN", (0.20, 0.02, 0.98))
        self._link(self.focus_fan, self.studio)
        self.focus_turb = self._empty("FOCUS_TURB", (4.95, -0.10, 0.48))
        self._link(self.focus_turb, self.studio)
        self._lights(focus)
        self._cameras()
        self._world()
        self._animate()
        self._render_settings(self.scene)
        self._render_settings(self.cut_scene)

    def _lights(self, focus):
        specs = [
            ("Key", (-1.5, -6.5, 5.5), 6.0, 3.2, 250.0, (1.0, 0.97, 0.93)),
            ("Fill", (7.5, 5.5, 3.2), 5.0, 3.0, 60.0, (0.82, 0.88, 1.0)),
            ("Rim", (9.5, -3.5, 2.4), 2.2, 5.0, 180.0, (1.0, 0.95, 0.90)),
            ("Inlet", (-2.2, -1.4, 1.2), 1.4, 1.0, 160.0, (1.0, 0.98, 0.95)),
        ]
        for name, loc, sx, sy, energy, color in specs:
            light = bpy.data.lights.new(name, "AREA")
            light.energy = energy
            light.color = color
            light.shape = "RECTANGLE"
            light.size = sx
            light.size_y = sy
            obj = bpy.data.objects.new("Light_" + name, light)
            obj.location = loc
            obj.visible_camera = False
            con = obj.constraints.new("TRACK_TO")
            con.target = focus
            con.track_axis = "TRACK_NEGATIVE_Z"
            con.up_axis = "UP_Y"
            self._link(obj, self.studio)

    def _look_camera(self, name, location, target, lens, fstop, ortho=False, ortho_scale=10.0):
        cam = bpy.data.cameras.new(name)
        cam.lens = lens
        cam.clip_start = 0.02
        cam.clip_end = 200.0
        cam.dof.use_dof = not ortho
        cam.dof.focus_object = target
        cam.dof.aperture_fstop = fstop
        if ortho:
            cam.type = "ORTHO"
            cam.ortho_scale = ortho_scale
            cam.sensor_fit = "HORIZONTAL"
        obj = bpy.data.objects.new(name, cam)
        obj.location = location
        con = obj.constraints.new("TRACK_TO")
        con.target = target
        con.track_axis = "TRACK_NEGATIVE_Z"
        con.up_axis = "UP_Y"
        self._link(obj, self.studio)
        return obj

    def _cameras(self):
        f = self.focus
        self.cam_hero = self._look_camera("CAM_Hero", (-3.4, -6.6, 2.55), f, 38.0, 8.0)
        self.cam_side = self._look_camera("CAM_Side", (3.9, -16.0, 0.15), self._empty_linked("FOCUS_SIDE", (3.9, 0.0, 0.12)), 80.0, 16.0, ortho=True, ortho_scale=11.6)
        self.cam_fan = self._look_camera("CAM_Fan", (-1.05, -0.72, 0.55), self.focus_fan, 75.0, 5.6)
        self.cam_turb = self._look_camera("CAM_Turbine", (4.35, -0.95, 0.05), self.focus_turb, 65.0, 8.0)
        self.cam_rivet = self._look_camera("CAM_Rivet", self._rivet_cam_loc(), self.focus_rivet_empty, 80.0, 8.0)
        # Turntable rig. Camera is parented to a rotating empty and tracks a fixed point.
        rig = self._empty("RIG_TURNTABLE", (3.5, 0.0, 0.35))
        self._link(rig, self.studio)
        self.rig = rig
        cam = bpy.data.cameras.new("CAM_Turntable")
        cam.lens = 40.0
        cam.clip_start = 0.05
        cam.clip_end = 200.0
        cam.dof.use_dof = False
        obj = bpy.data.objects.new("CAM_Turntable", cam)
        obj.parent = rig
        obj.location = (-9.2, -7.6, 2.55)
        con = obj.constraints.new("TRACK_TO")
        con.target = f
        con.track_axis = "TRACK_NEGATIVE_Z"
        con.up_axis = "UP_Y"
        self._link(obj, self.studio)
        self.cam_turn = obj
        # Cutaway fly-through. Locations are keyframed; Track To follows a moving empty.
        self.fly_target = self._empty("FOCUS_FLY", (0.4, -0.1, 0.45))
        self._link(self.fly_target, self.studio)
        self.cam_fly = self._look_camera("CAM_Fly", (-2.6, -2.15, 1.15), self.fly_target, 28.0, 8.0)
        self.scene.camera = self.cam_hero
        self.cut_scene.camera = self.cam_turb

    def _empty_linked(self, name, location):
        obj = self._empty(name, location)
        self._link(obj, self.studio)
        return obj

    def _rivet_cam_loc(self):
        target = self.focus_rivet or (1.3, -1.4, 0.6)
        # Sit just off the skin, looking slightly along a longitudinal rivet row.
        y, z = target[1], target[2]
        radial = math.hypot(y, z) or 1.0
        scale = (radial + 0.22) / radial
        return (target[0] - 0.16, y * scale, z * scale + 0.04)

    def _world(self):
        world = bpy.data.worlds.new("StudioWorld")
        self.scene.world = world
        self.cut_scene.world = world
        world.use_nodes = True
        nt = world.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputWorld")
        bg = nt.nodes.new("ShaderNodeBackground")
        env = nt.nodes.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(str(HDR_PATH), check_existing=True)
        env.image.filepath = "//hdri/studio_small_09_2k.hdr"
        # Rotate the studio so the softbox sits behind the camera, not beside the nozzle.
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        flip = nt.nodes.new("ShaderNodeVectorMath")
        flip.operation = "SCALE"
        flip.inputs["Scale"].default_value = -1.0
        rot = nt.nodes.new("ShaderNodeVectorRotate")
        rot.rotation_type = "Z_AXIS"
        rot.inputs["Angle"].default_value = math.radians(210.0)
        nt.links.new(geo.outputs["Incoming"], flip.inputs["Vector"])
        nt.links.new(flip.outputs["Vector"], rot.inputs["Vector"])
        nt.links.new(rot.outputs["Vector"], env.inputs["Vector"])
        nt.links.new(env.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = 0.85
        nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    def _animate(self):
        try:
            bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
        except Exception as exc:
            print("interpolation preference skipped", exc)
        end = params.FRAME_END
        start = params.FRAME_START
        frac = (end - start) / (end - start + 1)
        for spool, turns in ((self.lp, 3.5), (self.hp, 11.2)):
            spool.rotation_mode = "XYZ"
            spool.rotation_euler = (0.0, 0.0, 0.0)
            spool.keyframe_insert("rotation_euler", frame=start)
            spool.rotation_euler[0] = -turns * math.tau * frac
            spool.keyframe_insert("rotation_euler", frame=end)
        self.rig.rotation_mode = "XYZ"
        self.rig.rotation_euler = (0.0, 0.0, 0.0)
        self.rig.keyframe_insert("rotation_euler", frame=start)
        self.rig.rotation_euler[2] = math.tau * frac
        self.rig.keyframe_insert("rotation_euler", frame=end)
        # Fly-through uses bezier keys. Set them after the linear preference by forcing handles.
        fly_keys = [
            (1, (-2.7, -2.25, 1.20), (0.35, -0.15, 0.55)),
            (48, (0.9, -1.85, 0.80), (1.7, -0.10, 0.30)),
            (96, (3.15, -1.70, 0.55), (3.7, -0.05, 0.22)),
            (144, (5.35, -1.80, 0.65), (5.9, -0.05, 0.32)),
            (192, (8.15, -2.30, 1.05), (7.15, -0.10, 0.25)),
        ]
        for frame, loc, tgt in fly_keys:
            self.cam_fly.location = loc
            self.cam_fly.keyframe_insert("location", frame=frame)
            self.fly_target.location = tgt
            self.fly_target.keyframe_insert("location", frame=frame)
        for obj in (self.lp, self.hp, self.rig):
            action = obj.animation_data.action if obj.animation_data else None
            if not action:
                continue
            for fc in _fcurves(action):
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
        for obj in (self.cam_fly, self.fly_target):
            action = obj.animation_data.action if obj.animation_data else None
            if not action:
                continue
            for fc in _fcurves(action):
                for kp in fc.keyframe_points:
                    kp.interpolation = "BEZIER"
                    kp.handle_left_type = "AUTO_CLAMPED"
                    kp.handle_right_type = "AUTO_CLAMPED"
        for scene in (self.scene, self.cut_scene):
            scene.frame_start = start
            scene.frame_end = end
            scene.render.fps = params.FPS
        self.scene.frame_set(start)

    def _render_settings(self, scene):
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 96
        scene.cycles.use_adaptive_sampling = True
        scene.cycles.adaptive_threshold = 0.02
        scene.cycles.use_denoising = True
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
        scene.cycles.max_bounces = 8
        scene.cycles.diffuse_bounces = 3
        scene.cycles.glossy_bounces = 3
        scene.cycles.transmission_bounces = 4
        scene.cycles.volume_bounces = 0
        scene.cycles.transparent_max_bounces = 6
        scene.cycles.caustics_reflective = False
        scene.cycles.caustics_refractive = False
        scene.cycles.use_light_tree = True
        scene.cycles.sample_clamp_indirect = 12.0
        scene.cycles.blur_glossy = 0.6
        scene.render.resolution_x = 1920
        scene.render.resolution_y = 1080
        scene.render.resolution_percentage = 100
        scene.render.use_motion_blur = False
        scene.render.film_transparent = False
        scene.render.use_stamp = False
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - Medium High Contrast"
        scene.view_settings.exposure = 0.0
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1.0
        scene["engine"] = "Conceptual high-bypass turbofan with augmentor"
        scene["units"] = "meters"
        scene["cut_wedge_deg"] = "[-135, -45] atan2(y, z)"

    def report(self):
        def walk(col):
            polys = 0
            objs = 0
            for obj in col.objects:
                objs += 1
                if obj.type == "MESH":
                    polys += len(obj.data.polygons)
            return polys, objs

        out = {}
        for name, col in (
            ("full", self.full), ("cut", self.cut), ("common", self.common),
            ("studio", self.studio), ("glow_full", self.glow_full), ("glow_cut", self.glow_cut),
        ):
            polys, objs = walk(col)
            out[name] = {"objects": objs, "mesh_polygons": polys}
        full_scene_objs = len(self.scene.objects)
        cut_scene_objs = len(self.cut_scene.objects)
        out["scene_objects_full"] = full_scene_objs
        out["scene_objects_cut"] = cut_scene_objs
        out["rows"] = self.stats["rows"]
        out["mesh_datablocks"] = len(bpy.data.meshes)
        out["materials"] = len(bpy.data.materials)
        # Realized fastener / hole polygons on a sample of scatter curves.
        dg = bpy.context.evaluated_depsgraph_get()
        realized = 0
        counted = 0
        for obj in self.rivet_curves:
            if obj.name not in self.scene.objects:
                continue
            ev = obj.evaluated_get(dg)
            me = ev.to_mesh()
            realized += len(me.polygons)
            counted += 1
            ev.to_mesh_clear()
        out["scatter_curves_in_full_scene"] = counted
        out["scatter_realized_polygons_full"] = realized
        return out


def _iter_channelbags(strip):
    """Blender 4.4+ layered actions; 5.2 exposes channelbag() as a method."""
    bags = []
    channelbags = getattr(strip, "channelbags", None)
    if channelbags is not None:
        try:
            bags.extend(list(channelbags))
        except TypeError:
            pass
    channelbag = getattr(strip, "channelbag", None)
    if callable(channelbag):
        try:
            one = channelbag()
        except TypeError:
            one = None
        if one is not None and one not in bags:
            bags.append(one)
    elif channelbag is not None and hasattr(channelbag, "fcurves"):
        bags.append(channelbag)
    return bags


def _bag_fcurves(bag):
    fcurves = getattr(bag, "fcurves", None)
    if fcurves is None:
        return []
    if callable(fcurves):
        fcurves = fcurves()
    try:
        return list(fcurves)
    except TypeError:
        return []


def _fcurves(action):
    found = []
    seen = set()

    def add(fc):
        key = id(fc)
        if key not in seen:
            seen.add(key)
            found.append(fc)

    if hasattr(action, "layers"):
        for layer in action.layers:
            for strip in layer.strips:
                for bag in _iter_channelbags(strip):
                    for fc in _bag_fcurves(bag):
                        add(fc)
    legacy = getattr(action, "fcurves", None)
    if legacy is not None and not callable(legacy):
        try:
            for fc in legacy:
                add(fc)
        except TypeError:
            pass
    return found


def build():
    started = time.time()
    print("Building turbofan...")
    builder = Builder()
    builder.build_spools()
    print("  spools")
    builder.build_fan()
    print("  fan")
    builder.build_compressors()
    print("  compressors")
    builder.build_combustor()
    print("  combustor")
    builder.build_turbines()
    print("  turbines")
    builder.build_bypass_and_mixer()
    print("  bypass")
    builder.build_afterburner()
    print("  afterburner")
    builder.build_nacelle()
    print("  nacelle")
    builder.build_bearings()
    print("  bearings")
    builder.build_studio()
    print("  studio")
    # Drop orphan meshes from prototypes.
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    stats = builder.report()
    stats["build_seconds"] = round(time.time() - started, 2)
    stats_path = BLEND_DIR / "build_stats.json"
    stats_path.write_text(json.dumps(stats, indent=2))
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_PATH))
    print(json.dumps(stats, indent=2))
    print("Saved", BLEND_PATH, "in", stats["build_seconds"], "s")
