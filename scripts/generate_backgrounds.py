#!/usr/bin/env python3
"""Generate the default background GIFs (procedural, license-free).

Six animated scenes, each built with its own technique for a round LCD
with white text in the middle (one module each in backgrounds/):

- liquid (caustics.py): underwater caustics. A looping wave spectrum
  refracts a dense grid of light rays onto the floor; where rays converge,
  light gathers into the moving bright network of a pool floor.
- cpu (fire.py): a ring of fire. The value sits on the dark hearth;
  around it flames rise outward with sparks, built like a fire shader
  and made to loop.
- ram (die.py): a memory die on a dark board, copper traces with data
  pulses running in and out.
- gpu (globe.py): wireframe globe. A geodesic sphere spins about one of
  its five-fold axes, near edges bright, far edges dim.
- temp (plasma.py): plasma globe. Filaments reach from a core between the
  two values to the glass, swaying and flaring where they touch it.
- video (sunset.py): an evening landscape with morphing clouds and a
  rippling lake, the stand-in loop behind the video face.

Every moving part is periodic over the loop (integer temporal frequencies,
patterns that travel whole periods, a spin by exactly one symmetry step),
so each GIF loops seamlessly. Colors come from gradient palettes
interpolated in OKLab, or, for the fire, the die and the lake, from a
palette fitted to their rendered frames. Pixels that stay the same between frames (disc,
backdrop, the corners outside the round display) cost almost nothing in
the GIF, which keeps uploads small: every rendered tile is a device
upload, and the Kraken's image endpoint is fragile.

Needs numpy (only this script; the daemon does not). Regenerate with:

    python3 scripts/generate_backgrounds.py [output_dir] [name ...]

The shipped assets/ were produced by exactly this script; tweak a scene's
colors or its render() and rebuild your own set.
"""

import sys
from pathlib import Path

import numpy as np
from backgrounds import caustics, die, fire, globe, plasma, sunset
from backgrounds.common import FRAMES, save_gif, save_gif_true_color

SCENES = {"liquid": caustics, "cpu": fire, "ram": die, "gpu": globe, "temp": plasma,
          "video": sunset}


def generate(out_dir: Path, names: list[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        scene = SCENES[name]
        # fixed seed per scene: rebuilding gives the same animation
        rng = np.random.default_rng(sum(map(ord, name)))
        target = out_dir / f"{name}.gif"
        if hasattr(scene, "COLORS"):  # true-color scene, palette found per loop
            path = save_gif_true_color(target, scene.render(rng), scene.COLORS)
        else:
            path = save_gif(target, scene.render(rng), scene.PALETTE)
        print(f"{path}  {path.stat().st_size / 2**20:.2f} MB, {FRAMES} frames")


if __name__ == "__main__":
    args = sys.argv[1:]
    out = Path(args.pop(0)) if args and args[0] not in SCENES else \
        Path(__file__).resolve().parent.parent / "assets"
    unknown = [a for a in args if a not in SCENES]
    if unknown:
        sys.exit(f"unknown scene(s): {', '.join(unknown)}; known: {', '.join(SCENES)}")
    generate(out, args or list(SCENES))
