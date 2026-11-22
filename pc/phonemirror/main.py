"""PhoneMirror PC client: shows the iPhone screen and maps keyboard/mouse to touches."""
import argparse
import sys
import threading

from . import geometry, mapping
from . import protocol as P
from .session import Session, usb_connect_fn

HELP = "F1 edit  F2 stick  F3 overlay  F4 look-center  F5 save  F6 rotate  F7 resync  ` / Ctrl mouse-lock"
_FUNCTION_KEYS = {"f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "escape"}


class State:
    def __init__(self, profile_name):
        self.lock = threading.Lock()
        self.frame = None            # latest portrait RGB array
        self.orientation = 1
        self.frame_id = 0
        self.status = "starting..."
        self.profile = mapping.load_profile(profile_name)
        self.edit = False
        self.stick_mode = False
        self.look_mode = False
        self.mouse_locked = False
        self.show_overlay = False
        self.pending = None          # (x, y) waiting for a key press in edit mode
        self.message = ""

    def on_frame(self, frame, orientation):
        with self.lock:
            self.frame, self.orientation = frame, orientation
            self.frame_id += 1

    def on_status(self, text):
        self.status = text

    def frame_size(self):
        f = self.frame
        return (f.shape[1], f.shape[0]) if f is not None else mapping.DEFAULT_FRAME_SIZE

    def rotation(self):
        return geometry.effective_rotation(self.orientation, self.profile.get("rotation_offset", 0))


def run(args):
    import pygame
    from .decoder import H264Decoder

    state = State(args.profile)
    session = Session(lambda: usb_connect_fn(args.port), H264Decoder, state.on_frame, state.on_status)
    mapper = mapping.InputMapper(state.profile, session.send_touch, state.frame_size)

    pygame.init()
    pygame.display.set_caption("PhoneMirror")
    screen = pygame.display.set_mode((540, 960), pygame.RESIZABLE)
    font = pygame.font.SysFont(None, 22)
    clock = pygame.time.Clock()
    surface, surface_key, view_rect = None, None, pygame.Rect(0, 0, 1, 1)
    session.start()

    def to_portrait(pos):
        if not view_rect.collidepoint(pos):
            return None
        u = (pos[0] - view_rect.x) / view_rect.w
        v = (pos[1] - view_rect.y) / view_rect.h
        return geometry.view_to_portrait(u, v, state.rotation())

    def view_px(x, y):
        u, v = geometry.portrait_to_view(x, y, state.rotation())
        return int(view_rect.x + u * view_rect.w), int(view_rect.y + v * view_rect.h)

    def add_binding(binding):
        state.profile["bindings"].append(binding)
        try:
            mapping.validate_profile(state.profile)
        except ValueError as e:
            state.profile["bindings"].pop()
            state.message = str(e)

    running = True
    try:
        while running:
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    running = False
                elif e.type == getattr(pygame, "WINDOWFOCUSLOST", -1):
                    mapper.release_all()
                elif e.type == pygame.KEYDOWN:
                    name = pygame.key.name(e.key)
                    if name == "f1":
                        mapper.release_all()
                        state.edit, state.pending, state.stick_mode = not state.edit, None, False
                    elif name == "f2" and state.edit:
                        state.stick_mode, state.pending, state.look_mode = True, None, False
                    elif name == "f4" and state.edit:
                        state.look_mode, state.pending, state.stick_mode = True, None, False
                    elif name in ("`", "left ctrl", "right ctrl") and not state.edit:
                        state.mouse_locked = not state.mouse_locked
                        pygame.event.set_grab(state.mouse_locked)
                        pygame.mouse.set_visible(not state.mouse_locked)
                        if state.mouse_locked:
                            cl = state.profile.get("center_look", {"x": 0.65, "y": 0.50})
                            mapper.look_down(cl.get("x", 0.65), cl.get("y", 0.50))
                            state.message = "Mouse Locked (Camera Look)"
                        else:
                            mapper.look_up()
                            state.message = "Mouse Unlocked"
                    elif name == "f3":
                        state.show_overlay = not state.show_overlay
                    elif name == "f5":
                        try:
                            state.message = f"saved {mapping.save_profile(state.profile)}"
                        except (OSError, ValueError) as err:
                            state.message = f"save failed: {err}"
                    elif name == "f6":
                        state.profile["rotation_offset"] = (state.profile.get("rotation_offset", 0) + 90) % 360
                    elif name == "f7":
                        session.request_keyframe()
                    elif state.edit:
                        if name == "escape":
                            state.pending, state.stick_mode = None, False
                        elif state.pending and name not in _FUNCTION_KEYS:
                            add_binding({"type": "button", "key": name, "x": state.pending[0], "y": state.pending[1]})
                            state.pending = None
                    else:
                        mapper.key_down(name)
                elif e.type == pygame.KEYUP:
                    mapper.key_up(pygame.key.name(e.key))
                elif e.type == pygame.MOUSEBUTTONDOWN:
                    p = to_portrait(e.pos)
                    if p is None:
                        continue
                    if state.edit and e.button == 1:
                        if state.stick_mode:
                            add_binding({"type": "stick", "x": p[0], "y": p[1], "radius": 0.10,
                                         "keys": {"up": "w", "left": "a", "down": "s", "right": "d"}})
                            state.stick_mode = False
                        elif state.look_mode:
                            state.profile["center_look"] = {"enabled": True, "x": p[0], "y": p[1]}
                            state.look_mode = False
                            state.message = f"Set Camera Look Center: {p[0]:.2f}, {p[1]:.2f}"
                        else:
                            state.pending = p
                    elif state.edit and e.button == 3 and state.profile["bindings"]:
                        nearest = min(state.profile["bindings"], key=lambda b: (b["x"] - p[0]) ** 2 + (b["y"] - p[1]) ** 2)
                        if (nearest["x"] - p[0]) ** 2 + (nearest["y"] - p[1]) ** 2 < 0.05 ** 2:
                            state.profile["bindings"].remove(nearest)
                    elif not state.edit and e.button == 1:
                        mapper.mouse_down(*p)
                elif e.type == pygame.MOUSEMOTION and not state.edit:
                    if state.mouse_locked:
                        dx, dy = e.rel
                        sens = state.profile.get("sensitivity", 1.0) * 0.0015
                        cl = state.profile.get("center_look", {"x": 0.65, "y": 0.50})
                        mapper.look_delta(dx * sens, dy * sens, cl.get("x", 0.65), cl.get("y", 0.50))
                    else:
                        p = to_portrait(e.pos)
                        if p is not None:
                            mapper.mouse_move(*p)
                elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                    mapper.mouse_up()

            screen = pygame.display.get_surface()
            screen.fill((18, 18, 22))
            with state.lock:
                frame, fid = state.frame, state.frame_id
            if frame is not None:
                rot = state.rotation()
                if surface_key != (fid, rot):
                    shown = geometry.rotate_frame(frame, rot)
                    vh, vw = shown.shape[:2]
                    surface = pygame.image.frombuffer(shown.tobytes(), (vw, vh), "RGB")
                    surface_key = (fid, rot)
                sw, sh = screen.get_size()
                scale = min(sw / surface.get_width(), sh / surface.get_height())
                size = (max(1, int(surface.get_width() * scale)), max(1, int(surface.get_height() * scale)))
                view_rect = pygame.Rect((sw - size[0]) // 2, (sh - size[1]) // 2, *size)
                screen.blit(pygame.transform.scale(surface, size), view_rect.topleft)

                if state.edit or state.show_overlay:
                    rot_wide = rot in (0, 180)
                    for b in state.profile["bindings"]:
                        cx, cy = view_px(b["x"], b["y"])
                        if b["type"] == "stick":
                            radius = int(b["radius"] * (view_rect.w if rot_wide else view_rect.h))
                            pygame.draw.circle(screen, (80, 200, 255), (cx, cy), max(radius, 6), 2)
                            label = "".join(b["keys"][d] for d in ("up", "left", "down", "right"))
                        else:
                            pygame.draw.circle(screen, (255, 200, 60), (cx, cy), 14, 2)
                            label = b["key"]
                        screen.blit(font.render(label, True, (255, 255, 255)), (cx - 10, cy - 8))
                    if state.pending:
                        px, py = view_px(*state.pending)
                        pygame.draw.circle(screen, (255, 80, 80), (px, py), 16, 3)
            if frame is None:
                msg = font.render(state.status, True, (220, 220, 220))
                screen.blit(msg, ((screen.get_width() - msg.get_width()) // 2, screen.get_height() // 2))

            lines = [HELP]
            if state.edit:
                lines.append("EDIT: " + ("click stick centre" if state.stick_mode else
                                         "press a key to bind" if state.pending else
                                         "click a spot (right-click deletes, F2 adds a stick)"))
            if state.message:
                lines.append(state.message)
            lines.append(f"[{state.profile['name']}] {state.status}")
            for i, text in enumerate(lines):
                screen.blit(font.render(text, True, (200, 200, 200), (0, 0, 0)), (6, 6 + i * 20))
            pygame.display.flip()
            clock.tick(120)
    finally:
        mapper.release_all()
        session.stop()
        pygame.quit()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="phonemirror", description="Mirror an iPhone over USB and control it from the keyboard.")
    ap.add_argument("--profile", default="default", help="binding profile name")
    ap.add_argument("--port", type=int, default=P.DEVICE_PORT, help="device port")
    ap.add_argument("--list", action="store_true", help="list USB devices and exit")
    args = ap.parse_args(argv)
    if args.list:
        from . import usbmux
        try:
            for d in usbmux.list_devices():
                print(d)
        except Exception as e:  # noqa: BLE001
            print(f"cannot reach usbmuxd: {e}", file=sys.stderr)
            return 1
        return 0
    run(args)
    return 0
