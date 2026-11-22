"""Orientation handling. Touch coordinates are always normalised to the device's portrait-up
screen (x right, y down, 0..1). The video frame is portrait pixels plus an orientation tag.
`rotation` below is the clockwise rotation (0/90/180/270) applied to show the frame upright.
"""
import numpy as np

# CGImagePropertyOrientation -> clockwise rotation needed to display upright
_ORIENTATION_TO_ROTATION = {1: 0, 3: 180, 6: 90, 8: 270}


def orientation_to_rotation(orientation):
    return _ORIENTATION_TO_ROTATION.get(orientation, 0)


def effective_rotation(orientation, offset=0):
    return (orientation_to_rotation(orientation) + offset) % 360


def rotate_frame(frame, rotation):
    """Rotate an HxWxC array clockwise by rotation degrees."""
    k = (rotation // 90) % 4
    return frame if k == 0 else np.ascontiguousarray(np.rot90(frame, -k))


def view_to_portrait(u, v, rotation):
    """Point in the displayed (rotated) image, normalised, -> device portrait coordinates."""
    r = rotation % 360
    if r == 0:
        return u, v
    if r == 90:
        return v, 1.0 - u
    if r == 180:
        return 1.0 - u, 1.0 - v
    if r == 270:
        return 1.0 - v, u
    raise ValueError("rotation must be a multiple of 90")


def portrait_to_view(x, y, rotation):
    r = rotation % 360
    if r == 0:
        return x, y
    if r == 90:
        return 1.0 - y, x
    if r == 180:
        return 1.0 - x, 1.0 - y
    if r == 270:
        return y, 1.0 - x
    raise ValueError("rotation must be a multiple of 90")
