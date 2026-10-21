"""Switch the open blend file between the complete engine and the quarter cutaway.

Usage inside Blender's Python console, or:

    blender turbofan.blend -P scripts/toggle_cutaway.py -- full
    blender turbofan.blend -P scripts/toggle_cutaway.py -- cutaway

The file stores two scenes that share mesh data:

- Full: running nacelle, every blade, no section cut
- Cutaway: the same engine with a 90 degree wedge removed on the lower
  port side and flat cut-face colors (red LP spool, blue HP spool,
  orange combustor, grey casings, purple augmentor)

Flame glow lives in GlowFull / GlowCut so it can be hidden on its own
by excluding that collection in the outliner.
"""

import sys

import bpy


def main():
    argv = sys.argv
    choice = "cutaway"
    if "--" in argv:
        args = argv[argv.index("--") + 1 :]
        if args:
            choice = args[0].lower()
    if choice in ("full", "complete", "external"):
        scene_name = "Full"
    elif choice in ("cut", "cutaway", "section"):
        scene_name = "Cutaway"
    else:
        raise SystemExit(f"unknown mode {choice!r}; use full or cutaway")
    scene = bpy.data.scenes[scene_name]
    switched = False
    for window in bpy.context.window_manager.windows:
        window.scene = scene
        switched = True
    if not switched:
        print("No window; scene is selected only when a UI is open.")
    print(f"Active scene: {scene_name}")
    print("Cameras:", ", ".join(o.name for o in scene.objects if o.type == "CAMERA"))


if __name__ == "__main__":
    main()
