"""Key/mouse -> touch mapping and profile storage (pure logic, no GUI dependencies)."""
import json
import math
import os
import re
from pathlib import Path

from .protocol import TOUCH_DOWN, TOUCH_MOVE, TOUCH_UP

MOUSE_FINGER = 31
DEFAULT_FRAME_SIZE = (1125, 2436)  # iPhone X portrait pixels, used until the first frame arrives
_DIRS = ("up", "left", "down", "right")


def default_profile():
    return {
        "name": "default",
        "rotation_offset": 0,
        "bindings": [
            {"type": "stick", "x": 0.18, "y": 0.72, "radius": 0.10,
             "keys": {"up": "w", "left": "a", "down": "s", "right": "d"}},
            {"type": "button", "key": "space", "x": 0.88, "y": 0.62},
            {"type": "button", "key": "e", "x": 0.80, "y": 0.45},
        ],
    }


def validate_profile(p):
    if not isinstance(p, dict) or not isinstance(p.get("bindings"), list):
        raise ValueError("profile must be an object with a 'bindings' list")
    if p.get("rotation_offset", 0) not in (0, 90, 180, 270):
        raise ValueError("rotation_offset must be 0, 90, 180 or 270")
    if len(p["bindings"]) > MOUSE_FINGER:
        raise ValueError("too many bindings")
    for b in p["bindings"]:
        kind = b.get("type")
        for axis in ("x", "y"):
            if not isinstance(b.get(axis), (int, float)) or not 0 <= b[axis] <= 1:
                raise ValueError(f"binding {axis} must be a number in 0..1")
        if kind == "button":
            if not isinstance(b.get("key"), str) or not b["key"]:
                raise ValueError("button needs a 'key'")
        elif kind == "stick":
            keys = b.get("keys", {})
            if set(keys) != set(_DIRS) or not all(isinstance(k, str) and k for k in keys.values()):
                raise ValueError("stick needs keys for up/left/down/right")
            if not isinstance(b.get("radius"), (int, float)) or not 0 < b["radius"] <= 0.5:
                raise ValueError("stick radius must be in (0, 0.5]")
        else:
            raise ValueError(f"unknown binding type: {kind!r}")
    return p


def profiles_dir():
    base = os.environ.get("PHONEMIRROR_HOME") or str(Path.home() / ".phonemirror")
    d = Path(base) / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(name):
    if not re.fullmatch(r"[A-Za-z0-9_\- ]{1,64}", name):
        raise ValueError("profile name may only contain letters, digits, space, - and _")
    return name


def load_profile(name):
    path = profiles_dir() / f"{_safe_name(name)}.json"
    if not path.exists():
        profile = default_profile()
        profile["name"] = name
        return profile
    with open(path, "r", encoding="utf-8") as f:
        return validate_profile(json.load(f))


def save_profile(profile):
    validate_profile(profile)
    path = profiles_dir() / f"{_safe_name(profile['name'])}.json"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    os.replace(tmp, path)
    return path


class InputMapper:
    """Turns key/mouse events into touch events via send(action, finger, x, y).

    All coordinates are device-portrait normalised (0..1).
    """

    def __init__(self, profile, send, frame_size=lambda: DEFAULT_FRAME_SIZE):
        self.send = send
        self.frame_size = frame_size
        self.set_profile(profile)

    def set_profile(self, profile):
        self.release_all()
        self.profile = validate_profile(profile)
        self._down_keys = set()
        self._stick_active = {}
        self._held = {}        # finger -> (x, y) for held buttons
        self._mouse_down = False

    # -- helpers -----------------------------------------------------------
    def _buttons(self, key):
        for i, b in enumerate(self.profile["bindings"]):
            if b["type"] == "button" and b["key"] == key:
                yield i, b

    def _sticks(self, key):
        for i, b in enumerate(self.profile["bindings"]):
            if b["type"] == "stick" and key in b["keys"].values():
                yield i, b

    def _stick_target(self, b):
        pressed = {d for d, k in b["keys"].items() if k in self._down_keys}
        vx = (1 if "right" in pressed else 0) - (1 if "left" in pressed else 0)
        vy = (1 if "down" in pressed else 0) - (1 if "up" in pressed else 0)
        length = math.hypot(vx, vy)
        if length == 0:
            return None
        w, h = self.frame_size()
        aspect = w / h
        r = b["radius"]
        return (min(1, max(0, b["x"] + vx / length * r)),
                min(1, max(0, b["y"] + vy / length * r * aspect)))

    def _update_stick(self, i, b):
        target = self._stick_target(b)
        active = self._stick_active.get(i)
        if target is None:
            if active:
                self.send(TOUCH_UP, i, *active)
                del self._stick_active[i]
            return
        if not active:
            self.send(TOUCH_DOWN, i, b["x"], b["y"])
        self.send(TOUCH_MOVE, i, *target)
        self._stick_active[i] = target

    # -- events --------------------------------------------------------------
    def key_down(self, key):
        if key in self._down_keys:
            return
        self._down_keys.add(key)
        for i, b in self._buttons(key):
            self._held[i] = (b["x"], b["y"])
            self.send(TOUCH_DOWN, i, b["x"], b["y"])
        for i, b in self._sticks(key):
            self._update_stick(i, b)

    def key_up(self, key):
        if key not in self._down_keys:
            return
        self._down_keys.discard(key)
        for i, b in self._buttons(key):
            if i in self._held:
                self.send(TOUCH_UP, i, *self._held.pop(i))
        for i, b in self._sticks(key):
            self._update_stick(i, b)

    def mouse_down(self, x, y):
        self._mouse_down = True
        self._mouse_pos = (x, y)
        self.send(TOUCH_DOWN, MOUSE_FINGER, x, y)

    def mouse_move(self, x, y):
        if self._mouse_down:
            self._mouse_pos = (x, y)
            self.send(TOUCH_MOVE, MOUSE_FINGER, x, y)

    def mouse_up(self):
        if self._mouse_down:
            self._mouse_down = False
            self.send(TOUCH_UP, MOUSE_FINGER, *self._mouse_pos)

    def release_all(self):
        """Lift every finger (focus lost, edit mode, shutdown)."""
        if not hasattr(self, "_down_keys"):
            return
        for i, pos in list(self._held.items()):
            self.send(TOUCH_UP, i, *pos)
        for i, pos in list(self._stick_active.items()):
            self.send(TOUCH_UP, i, *pos)
        self.mouse_up()
        self._held.clear()
        self._stick_active.clear()
        self._down_keys.clear()
