"""Scenes for the default background GIFs, one module per scene.

Each scene module has a ``PALETTE`` (one or two color ramps) and a
``render(rng)`` that returns ``FRAMES`` brightness fields; ``common`` holds
the loop timing, the drawing helpers and the GIF encoder they share.
"""
