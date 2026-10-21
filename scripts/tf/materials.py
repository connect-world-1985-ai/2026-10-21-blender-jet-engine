"""PBR materials and the generated carbon-fiber maps."""

from __future__ import annotations

import math
import os

import bpy


def _bsdf(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (480, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (160, 0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat, nt, bsdf


def _set(node, name, value):
    if name in node.inputs:
        node.inputs[name].default_value = value


def _noise(nt, scale, detail, roughness, loc):
    n = nt.nodes.new("ShaderNodeTexNoise")
    n.location = loc
    n.inputs["Scale"].default_value = scale
    n.inputs["Detail"].default_value = detail
    n.inputs["Roughness"].default_value = roughness
    return n


def _ramp(nt, stops, loc):
    """stops: list of (position, (r,g,b,a))."""
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.location = loc
    el = ramp.color_ramp.elements
    el[0].position = stops[0][0]
    el[0].color = stops[0][1]
    el[1].position = stops[1][0]
    el[1].color = stops[1][1]
    for pos, col in stops[2:]:
        e = el.new(pos)
        e.color = col
    return ramp


def _obj_pos(nt, loc=(-600, 0)):
    tex = nt.nodes.new("ShaderNodeTexCoord")
    tex.location = loc
    return tex


def build_carbon_textures(folder):
    """Write a 2/2 twill albedo, roughness, and tangent-space normal."""
    os.makedirs(folder, exist_ok=True)
    n = 768
    yarn = 48
    albedo = [0.0] * (n * n * 4)
    rough = [0.0] * (n * n * 4)
    height = [0.0] * (n * n)

    def fract_hash(i, j):
        x = math.sin(i * 12.9898 + j * 78.233) * 43758.5453
        return x - math.floor(x)

    for j in range(n):
        v = j / yarn
        iv = int(v)
        fv = v - iv
        for i in range(n):
            u = i / yarn
            iu = int(u)
            fu = u - iu
            # 2/2 twill: warp is on top for two wefts, shifted one yarn per row.
            warp_top = ((iu - iv) % 4) < 2
            across = fu if warp_top else fv
            along = fv if warp_top else fu
            gap = across < 0.07 or across > 0.93
            fiber = (across * 5.0) % 1.0
            fiber_h = 1.0 - abs(fiber - 0.5) * 2.0
            fiber_h = fiber_h ** 0.55
            if gap:
                h = 0.04
                col = (0.025, 0.026, 0.030)
                rgh = 0.72
            else:
                h = 0.22 + 0.78 * fiber_h
                speckle = 0.90 + 0.10 * fract_hash(i, j)
                # A faint axial sheen difference between warp and weft.
                if warp_top:
                    col = (0.16 * speckle, 0.165 * speckle, 0.175 * speckle)
                else:
                    col = (0.22 * speckle, 0.225 * speckle, 0.235 * speckle)
                # Occasional lighter tow.
                if (iu + 3 * iv) % 11 == 0:
                    col = tuple(min(1.0, c * 1.25) for c in col)
                rgh = 0.28 + 0.22 * (1.0 - fiber_h)
                # Subtle along-tow variation.
                rgh *= 0.92 + 0.08 * fract_hash(int(along * 40), iu)
            height[j * n + i] = h
            o = (j * n + i) * 4
            albedo[o:o + 4] = (col[0], col[1], col[2], 1.0)
            rough[o:o + 4] = (rgh, rgh, rgh, 1.0)

    normal = [0.0] * (n * n * 4)
    strength = 3.2

    def h_at(x, y):
        return height[(y % n) * n + (x % n)]

    for j in range(n):
        for i in range(n):
            dx = (
                h_at(i + 1, j - 1) + 2 * h_at(i + 1, j) + h_at(i + 1, j + 1)
                - h_at(i - 1, j - 1) - 2 * h_at(i - 1, j) - h_at(i - 1, j + 1)
            )
            dy = (
                h_at(i - 1, j + 1) + 2 * h_at(i, j + 1) + h_at(i + 1, j + 1)
                - h_at(i - 1, j - 1) - 2 * h_at(i, j - 1) - h_at(i + 1, j - 1)
            )
            nx, ny, nz = -dx * strength, -dy * strength, 1.0
            inv = 1.0 / math.sqrt(nx * nx + ny * ny + nz * nz)
            o = (j * n + i) * 4
            normal[o] = nx * inv * 0.5 + 0.5
            normal[o + 1] = ny * inv * 0.5 + 0.5
            normal[o + 2] = nz * inv * 0.5 + 0.5
            normal[o + 3] = 1.0

    paths = {}
    for key, buf, space in (
        ("albedo", albedo, "sRGB"),
        ("roughness", rough, "Non-Color"),
        ("normal", normal, "Non-Color"),
    ):
        img = bpy.data.images.new(f"carbon_{key}", n, n, alpha=False, float_buffer=False)
        # Blender 5.2 drops pixel data on save if the colorspace is changed after the buffer write.
        img.colorspace_settings.name = space
        img.pixels.foreach_set(buf)
        path = os.path.join(folder, f"carbon_{key}.png")
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        # Keep the relative path the blend file will store.
        img.filepath = f"//textures/carbon_{key}.png"
        paths[key] = img
    return paths


def _carbon(images):
    mat, nt, bsdf = _bsdf("CarbonWeave")
    tex = _obj_pos(nt, (-900, 0))
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.location = (-700, 0)
    mapping.inputs["Scale"].default_value = (6.0, 3.0, 1.0)
    nt.links.new(tex.outputs["UV"], mapping.inputs["Vector"])
    alb = nt.nodes.new("ShaderNodeTexImage")
    alb.location = (-420, 180)
    alb.image = images["albedo"]
    rgh = nt.nodes.new("ShaderNodeTexImage")
    rgh.location = (-420, -40)
    rgh.image = images["roughness"]
    rgh.image.colorspace_settings.name = "Non-Color"
    nrm = nt.nodes.new("ShaderNodeTexImage")
    nrm.location = (-420, -260)
    nrm.image = images["normal"]
    nrm.image.colorspace_settings.name = "Non-Color"
    for node in (alb, rgh, nrm):
        nt.links.new(mapping.outputs["Vector"], node.inputs["Vector"])
    nmap = nt.nodes.new("ShaderNodeNormalMap")
    nmap.location = (-120, -240)
    nmap.inputs["Strength"].default_value = 0.85
    nt.links.new(nrm.outputs["Color"], nmap.inputs["Color"])
    nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    nt.links.new(alb.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(rgh.outputs["Color"], bsdf.inputs["Roughness"])
    _set(bsdf, "Metallic", 0.0)
    _set(bsdf, "Specular IOR Level", 0.45)
    _set(bsdf, "Coat Weight", 0.55)
    _set(bsdf, "Coat Roughness", 0.08)
    _set(bsdf, "Coat IOR", 1.50)
    _set(bsdf, "Anisotropic", 0.15)
    return mat


def _titanium():
    """Spanwise brush: fine stripes in U (around the section) kept subtle, color stays titanium."""
    mat, nt, bsdf = _bsdf("TitaniumBrushed")
    tex = _obj_pos(nt, (-860, 40))
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-660, 40)
    nt.links.new(tex.outputs["Object"], sep.inputs["Vector"])
    # Chordwise coordinate, high frequency, so the streaks run along the span.
    scale = nt.nodes.new("ShaderNodeMath")
    scale.location = (-460, 80)
    scale.operation = "MULTIPLY"
    scale.inputs[1].default_value = 180.0
    nt.links.new(sep.outputs["X"], scale.inputs[0])
    sine = nt.nodes.new("ShaderNodeMath")
    sine.location = (-280, 80)
    sine.operation = "SINE"
    nt.links.new(scale.outputs["Value"], sine.inputs[0])
    rough = nt.nodes.new("ShaderNodeMapRange")
    rough.location = (-80, 40)
    rough.inputs["From Min"].default_value = -1.0
    rough.inputs["From Max"].default_value = 1.0
    rough.inputs["To Min"].default_value = 0.22
    rough.inputs["To Max"].default_value = 0.38
    nt.links.new(sine.outputs["Value"], rough.inputs["Value"])
    nt.links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.location = (-80, -180)
    bump.inputs["Strength"].default_value = 0.04
    bump.inputs["Distance"].default_value = 0.0008
    nt.links.new(sine.outputs["Value"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    _set(bsdf, "Base Color", (0.64, 0.63, 0.60, 1.0))
    _set(bsdf, "Metallic", 1.0)
    _set(bsdf, "Anisotropic", 0.72)
    _set(bsdf, "Anisotropic Rotation", 0.25)
    _set(bsdf, "Specular IOR Level", 0.5)
    try:
        tang = nt.nodes.new("ShaderNodeTangent")
        tang.location = (-80, -360)
        tang.direction_type = "UV_MAP"
        tang.uv_map = "UVMap"
        nt.links.new(tang.outputs["Tangent"], bsdf.inputs["Tangent"])
    except Exception as exc:
        print("tangent node skipped", exc)
    return mat


def _paint():
    mat, nt, bsdf = _bsdf("NacellePaint")
    tex = _obj_pos(nt, (-980, 0))
    n1 = _noise(nt, 2.2, 4.0, 0.5, (-700, 200))
    n2 = _noise(nt, 28.0, 6.0, 0.6, (-700, -40))
    nt.links.new(tex.outputs["Object"], n1.inputs["Vector"])
    nt.links.new(tex.outputs["Object"], n2.inputs["Vector"])
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    geo.location = (-700, -280)
    wear = _ramp(
        nt,
        [
            (0.35, (0.0, 0.0, 0.0, 1.0)),
            (0.52, (0.0, 0.0, 0.0, 1.0)),
            (0.62, (1.0, 1.0, 1.0, 1.0)),
        ],
        (-420, -280),
    )
    nt.links.new(geo.outputs["Pointiness"], wear.inputs["Fac"])
    # Paint, yellow-green primer, bare aluminum.
    mix1 = nt.nodes.new("ShaderNodeMix")
    mix1.location = (-180, 160)
    mix1.data_type = "RGBA"
    mix1.inputs["A"].default_value = (0.72, 0.73, 0.71, 1.0)
    mix1.inputs["B"].default_value = (0.58, 0.59, 0.57, 1.0)
    nt.links.new(n1.outputs["Fac"], mix1.inputs["Factor"])
    mix2 = nt.nodes.new("ShaderNodeMix")
    mix2.location = (40, 160)
    mix2.data_type = "RGBA"
    mix2.inputs["B"].default_value = (0.34, 0.36, 0.24, 1.0)
    nt.links.new(mix1.outputs["Result"], mix2.inputs["A"])
    nt.links.new(wear.outputs["Color"], mix2.inputs["Factor"])
    nt.links.new(mix2.outputs["Result"], bsdf.inputs["Base Color"])
    rough = nt.nodes.new("ShaderNodeMapRange")
    rough.location = (-180, -40)
    rough.inputs["From Min"].default_value = 0.3
    rough.inputs["From Max"].default_value = 0.8
    rough.inputs["To Min"].default_value = 0.34
    rough.inputs["To Max"].default_value = 0.62
    nt.links.new(n2.outputs["Fac"], rough.inputs["Value"])
    nt.links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])
    _set(bsdf, "Metallic", 0.0)
    _set(bsdf, "Coat Weight", 0.12)
    _set(bsdf, "Coat Roughness", 0.18)
    _set(bsdf, "Specular IOR Level", 0.5)
    bump = nt.nodes.new("ShaderNodeBump")
    bump.location = (-180, -260)
    bump.inputs["Strength"].default_value = 0.018
    nt.links.new(n2.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def _aluminum():
    mat, nt, bsdf = _bsdf("AluminumLip")
    tex = _obj_pos(nt, (-700, 0))
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.location = (-480, 0)
    mapping.inputs["Scale"].default_value = (6.0, 40.0, 40.0)
    nt.links.new(tex.outputs["Object"], mapping.inputs["Vector"])
    noise = _noise(nt, 1.0, 6.0, 0.45, (-250, 40))
    nt.links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    ramp = nt.nodes.new("ShaderNodeMapRange")
    ramp.location = (-40, -80)
    ramp.inputs["To Min"].default_value = 0.16
    ramp.inputs["To Max"].default_value = 0.34
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Value"])
    nt.links.new(ramp.outputs["Result"], bsdf.inputs["Roughness"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.location = (-40, -240)
    bump.inputs["Strength"].default_value = 0.06
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    _set(bsdf, "Base Color", (0.78, 0.79, 0.80, 1.0))
    _set(bsdf, "Metallic", 1.0)
    _set(bsdf, "Anisotropic", 0.55)
    _set(bsdf, "Anisotropic Rotation", 0.0)
    return mat


def _temper():
    mat, nt, bsdf = _bsdf("TurbineTemper")
    tex = _obj_pos(nt, (-980, 80))
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-760, 80)
    nt.links.new(tex.outputs["Object"], sep.inputs["Vector"])
    noise = _noise(nt, 14.0, 5.0, 0.55, (-760, -160))
    nt.links.new(tex.outputs["Object"], noise.inputs["Vector"])
    # Wobble the axial coordinate so oxide bands are blotchy, not lathe-turned.
    wobble = nt.nodes.new("ShaderNodeMath")
    wobble.location = (-540, 0)
    wobble.operation = "ADD"
    nt.links.new(sep.outputs["X"], wobble.inputs[0])
    scale = nt.nodes.new("ShaderNodeMath")
    scale.location = (-540, -140)
    scale.operation = "MULTIPLY"
    scale.inputs[1].default_value = 0.18
    nt.links.new(noise.outputs["Fac"], scale.inputs[0])
    nt.links.new(scale.outputs["Value"], wobble.inputs[1])
    span = nt.nodes.new("ShaderNodeMapRange")
    span.location = (-320, 40)
    span.inputs["From Min"].default_value = 4.55
    span.inputs["From Max"].default_value = 6.35
    span.inputs["To Min"].default_value = 0.0
    span.inputs["To Max"].default_value = 1.0
    span.clamp = True
    nt.links.new(wobble.outputs["Value"], span.inputs["Value"])
    # Classic temper sequence: blue/purple on the NGV, straw through the LPT.
    ramp = _ramp(
        nt,
        [
            (0.00, (0.16, 0.22, 0.58, 1.0)),
            (0.16, (0.32, 0.16, 0.42, 1.0)),
            (0.34, (0.42, 0.24, 0.16, 1.0)),
            (0.52, (0.62, 0.42, 0.18, 1.0)),
            (0.72, (0.55, 0.46, 0.32, 1.0)),
            (1.00, (0.48, 0.48, 0.46, 1.0)),
        ],
        (-80, 80),
    )
    nt.links.new(span.outputs["Result"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    rough = nt.nodes.new("ShaderNodeMapRange")
    rough.location = (-80, -180)
    rough.inputs["To Min"].default_value = 0.32
    rough.inputs["To Max"].default_value = 0.58
    nt.links.new(noise.outputs["Fac"], rough.inputs["Value"])
    nt.links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.location = (-80, -360)
    bump.inputs["Strength"].default_value = 0.12
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    _set(bsdf, "Metallic", 1.0)
    _set(bsdf, "Anisotropic", 0.25)
    _set(bsdf, "Specular IOR Level", 0.45)
    return mat


def _ceramic():
    mat, nt, bsdf = _bsdf("CombustorTBC")
    tex = _obj_pos(nt, (-860, 0))
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.location = (-640, 0)
    mapping.inputs["Scale"].default_value = (6.0, 1.5, 1.5)
    nt.links.new(tex.outputs["Object"], mapping.inputs["Vector"])
    noise = _noise(nt, 1.0, 7.0, 0.62, (-400, 80))
    chips = _noise(nt, 9.0, 3.0, 0.4, (-400, -180))
    nt.links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    nt.links.new(tex.outputs["Object"], chips.inputs["Vector"])
    soot = _ramp(
        nt,
        [
            (0.35, (0.80, 0.74, 0.62, 1.0)),
            (0.62, (0.55, 0.46, 0.34, 1.0)),
            (0.85, (0.18, 0.14, 0.11, 1.0)),
        ],
        (-160, 120),
    )
    nt.links.new(noise.outputs["Fac"], soot.inputs["Fac"])
    chip_mask = _ramp(
        nt,
        [
            (0.72, (0.0, 0.0, 0.0, 1.0)),
            (0.82, (1.0, 1.0, 1.0, 1.0)),
        ],
        (-160, -80),
    )
    nt.links.new(chips.outputs["Fac"], chip_mask.inputs["Fac"])
    mix = nt.nodes.new("ShaderNodeMix")
    mix.location = (60, 40)
    mix.data_type = "RGBA"
    mix.inputs["B"].default_value = (0.22, 0.20, 0.18, 1.0)
    nt.links.new(soot.outputs["Color"], mix.inputs["A"])
    nt.links.new(chip_mask.outputs["Color"], mix.inputs["Factor"])
    nt.links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
    _set(bsdf, "Roughness", 0.72)
    _set(bsdf, "Metallic", 0.04)
    _set(bsdf, "Specular IOR Level", 0.35)
    bump = nt.nodes.new("ShaderNodeBump")
    bump.location = (60, -200)
    bump.inputs["Strength"].default_value = 0.35
    bump.inputs["Distance"].default_value = 0.004
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def _afterburner():
    mat, nt, bsdf = _bsdf("AfterburnerMetal")
    tex = _obj_pos(nt, (-760, 0))
    noise = _noise(nt, 8.0, 6.0, 0.55, (-480, 40))
    nt.links.new(tex.outputs["Object"], noise.inputs["Vector"])
    ramp = _ramp(
        nt,
        [
            (0.30, (0.07, 0.065, 0.06, 1.0)),
            (0.55, (0.16, 0.10, 0.07, 1.0)),
            (0.78, (0.12, 0.14, 0.22, 1.0)),
        ],
        (-220, 40),
    )
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    _set(bsdf, "Metallic", 0.9)
    _set(bsdf, "Roughness", 0.48)
    _set(bsdf, "Emission Color", (1.0, 0.28, 0.05, 1.0))
    _set(bsdf, "Emission Strength", 0.15)
    return mat


def _spinner():
    """White spinner with a single dark spiral so rotation is visible on the ground."""
    mat, nt, bsdf = _bsdf("SpinnerSpiral")
    tex = _obj_pos(nt, (-900, 0))
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-680, 0)
    nt.links.new(tex.outputs["Object"], sep.inputs["Vector"])
    ang = nt.nodes.new("ShaderNodeMath")
    ang.location = (-480, 80)
    ang.operation = "ARCTAN2"
    nt.links.new(sep.outputs["Y"], ang.inputs[0])
    nt.links.new(sep.outputs["Z"], ang.inputs[1])
    # turns = angle/tau + x * k
    div = nt.nodes.new("ShaderNodeMath")
    div.location = (-280, 120)
    div.operation = "DIVIDE"
    div.inputs[1].default_value = math.tau
    nt.links.new(ang.outputs["Value"], div.inputs[0])
    kx = nt.nodes.new("ShaderNodeMath")
    kx.location = (-280, -20)
    kx.operation = "MULTIPLY"
    kx.inputs[1].default_value = 3.4
    nt.links.new(sep.outputs["X"], kx.inputs[0])
    add = nt.nodes.new("ShaderNodeMath")
    add.location = (-80, 60)
    add.operation = "ADD"
    nt.links.new(div.outputs["Value"], add.inputs[0])
    nt.links.new(kx.outputs["Value"], add.inputs[1])
    fract = nt.nodes.new("ShaderNodeMath")
    fract.location = (80, 60)
    fract.operation = "FRACT"
    nt.links.new(add.outputs["Value"], fract.inputs[0])
    band = _ramp(
        nt,
        [
            (0.00, (0.02, 0.02, 0.025, 1.0)),
            (0.12, (0.02, 0.02, 0.025, 1.0)),
            (0.16, (0.90, 0.90, 0.88, 1.0)),
            (1.00, (0.90, 0.90, 0.88, 1.0)),
        ],
        (260, 60),
    )
    nt.links.new(fract.outputs["Value"], band.inputs["Fac"])
    nt.links.new(band.outputs["Color"], bsdf.inputs["Base Color"])
    _set(bsdf, "Roughness", 0.32)
    _set(bsdf, "Metallic", 0.0)
    _set(bsdf, "Coat Weight", 0.35)
    _set(bsdf, "Coat Roughness", 0.1)
    return mat


def _flame():
    mat, nt, _bsdf_unused = _bsdf("FlameGlow")
    # Replace principled with an emissive volume-like surface.
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (420, 0)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (160, 40)
    trans = nt.nodes.new("ShaderNodeBsdfTransparent")
    trans.location = (160, -140)
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (300, 0)
    tex = _obj_pos(nt, (-760, 0))
    noise = _noise(nt, 6.5, 6.0, 0.55, (-480, 40))
    nt.links.new(tex.outputs["Object"], noise.inputs["Vector"])
    ramp = _ramp(
        nt,
        [
            (0.28, (0.0, 0.0, 0.0, 1.0)),
            (0.48, (0.85, 0.08, 0.01, 1.0)),
            (0.70, (1.0, 0.42, 0.05, 1.0)),
            (0.90, (1.0, 0.85, 0.45, 1.0)),
        ],
        (-200, 40),
    )
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], emission.inputs["Color"])
    emission.inputs["Strength"].default_value = 8.0
    fac = nt.nodes.new("ShaderNodeMapRange")
    fac.location = (-200, -160)
    fac.inputs["From Min"].default_value = 0.42
    fac.inputs["From Max"].default_value = 0.78
    fac.inputs["To Min"].default_value = 0.0
    fac.inputs["To Max"].default_value = 1.0
    nt.links.new(noise.outputs["Fac"], fac.inputs["Value"])
    nt.links.new(fac.outputs["Result"], mix.inputs["Fac"])
    nt.links.new(trans.outputs["BSDF"], mix.inputs[1])
    nt.links.new(emission.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    try:
        mat.surface_render_method = "DITHERED"
    except Exception:
        pass
    return mat


def _flat(name, color, roughness=0.62):
    mat, _nt, bsdf = _bsdf(name)
    _set(bsdf, "Base Color", (*color, 1.0))
    _set(bsdf, "Roughness", roughness)
    _set(bsdf, "Metallic", 0.0)
    _set(bsdf, "Specular IOR Level", 0.3)
    return mat


def _metal(name, color, roughness=0.32, anisotropic=0.0):
    mat, _nt, bsdf = _bsdf(name)
    _set(bsdf, "Base Color", (*color, 1.0))
    _set(bsdf, "Metallic", 1.0)
    _set(bsdf, "Roughness", roughness)
    _set(bsdf, "Anisotropic", anisotropic)
    return mat


def build_materials(texture_dir):
    images = build_carbon_textures(texture_dir)
    mats = {
        "titanium": _titanium(),
        "paint": _paint(),
        "aluminum": _aluminum(),
        "carbon": _carbon(images),
        "temper": _temper(),
        "ceramic": _ceramic(),
        "afterburner": _afterburner(),
        "spinner": _spinner(),
        "flame": _flame(),
        "steel": _metal("Steel", (0.62, 0.63, 0.64), 0.28),
        "steel_dark": _metal("SteelDark", (0.28, 0.29, 0.30), 0.38),
        "abradable": _flat("Abradable", (0.72, 0.62, 0.28), 0.78),
        "gap": _flat("GapDark", (0.012, 0.012, 0.013), 0.7),
        "soot": _metal("Soot", (0.01, 0.01, 0.012), 0.55),
        "fastener": _metal("Fastener", (0.22, 0.23, 0.24), 0.42),
        "floor": _flat("StudioFloor", (0.045, 0.046, 0.050), 0.42),
        "cut_lp": _flat("CutLP", (0.78, 0.10, 0.08), 0.55),
        "cut_hp": _flat("CutHP", (0.10, 0.28, 0.78), 0.55),
        "cut_hot": _flat("CutHot", (0.85, 0.32, 0.08), 0.55),
        "cut_case": _flat("CutCase", (0.72, 0.73, 0.70), 0.5),
        "cut_ab": _flat("CutAB", (0.40, 0.22, 0.48), 0.55),
        "cut_stator": _flat("CutStator", (0.15, 0.55, 0.58), 0.55),
    }
    # Floor should pick up a little of the nacelle.
    _set(mats["floor"].node_tree.nodes["Principled BSDF"], "Specular IOR Level", 0.4)
    _set(mats["floor"].node_tree.nodes["Principled BSDF"], "Metallic", 0.0)
    return mats
