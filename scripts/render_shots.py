"""Render stills from the saved turbofan blend.

    blender -b 2026-10-21-blend/turbofan.blend -P scripts/render_shots.py -- preview
    blender -b 2026-10-21-blend/turbofan.blend -P scripts/render_shots.py -- final
    blender -b 2026-10-21-blend/turbofan.blend -P scripts/render_shots.py -- anim

Shot list is fixed. `preview` is 960x540 / 16 samples. `final` is 1920x1080
at the sample count stored in the file. `anim` renders the turntable on Full
and the fly-through on Cutaway, then this script does not encode video.
"""

import sys
import time
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "2026-10-21-renders"

SHOTS = [
    ("hero", "Full", "CAM_Hero"),
    ("cutaway", "Cutaway", "CAM_Hero"),
    ("side", "Full", "CAM_Side"),
    ("fan", "Full", "CAM_Fan"),
    ("turbine", "Cutaway", "CAM_Turbine"),
    ("rivets", "Full", "CAM_Rivet"),
]


def _mode():
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1 :]
    else:
        args = []
    return args[0] if args else "preview"


def _prepare(scene, samples, width, height):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.compression = 15
    scene.render.use_overwrite = True
    scene.frame_set(1)


def render_stills(mode):
    if mode == "preview":
        width, height, samples = 960, 540, 16
        folder = OUT / "preview"
    else:
        width, height, samples = 1920, 1080, 64
        folder = OUT
    folder.mkdir(parents=True, exist_ok=True)
    times = {}
    for name, scene_name, cam_name in SHOTS:
        scene = bpy.data.scenes[scene_name]
        scene.camera = bpy.data.objects[cam_name]
        _prepare(scene, samples, width, height)
        scene.render.filepath = str(folder / name)
        print(f"RENDER {name} scene={scene_name} cam={cam_name} {width}x{height} samples={samples}", flush=True)
        t0 = time.time()
        with bpy.context.temp_override(scene=scene):
            bpy.ops.render.render(write_still=True)
        times[name] = round(time.time() - t0, 1)
        print(f"DONE {name} {times[name]}s", flush=True)
    print("STILL_TIMES", times, flush=True)


def render_anim(which):
    """which: turntable | fly | both"""
    jobs = []
    if which in ("turntable", "both"):
        jobs.append(("turntable", "Full", "CAM_Turntable", 32))
    if which in ("fly", "both"):
        jobs.append(("flythrough", "Cutaway", "CAM_Fly", 16))
    width, height = 1280, 720
    for name, scene_name, cam_name, samples in jobs:
        scene = bpy.data.scenes[scene_name]
        scene.camera = bpy.data.objects[cam_name]
        _prepare(scene, samples, width, height)
        frames = OUT / "frames" / name
        frames.mkdir(parents=True, exist_ok=True)
        scene.render.filepath = str(frames / "frame_")
        scene.frame_start = 1
        scene.frame_end = 192
        print(f"ANIM {name} {width}x{height} samples={samples}", flush=True)
        t0 = time.time()
        with bpy.context.temp_override(scene=scene):
            bpy.ops.render.render(animation=True)
        print(f"DONE {name} {round(time.time() - t0, 1)}s", flush=True)


def main():
    mode = _mode()
    if mode in ("preview", "final"):
        render_stills(mode)
    elif mode in ("turntable", "fly", "both"):
        render_anim(mode)
    else:
        raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
