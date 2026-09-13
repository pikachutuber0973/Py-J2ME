import os
import sys
import threading
import time
import traceback

import pygame

from jvm.machine import ClassLoader, JavaObject, JavaThrowable
from jvm.engine import Engine
from midp.natives import NativeBridge, GraphicsSurface
from midp import keys as K

_KEYSTATE_BITS = {
    K.KEY_UP: 1 << 1, K.KEY_LEFT: 1 << 2, K.KEY_RIGHT: 1 << 5,
    K.KEY_DOWN: 1 << 6, K.KEY_FIRE: 1 << 8,
}


_HOT_PATH_LOCK_TIMEOUT = 0.02


class EmulatorEngine:
    def __init__(self, width=240, height=320, scale=2, log_callback=None,
                 force_continuous_repaint=False, target_fps=30, locale=None):
        self.width = width
        self.height = height
        self.scale = scale
        self.target_fps = target_fps
        self.force_continuous_repaint = force_continuous_repaint
        self.log_callback = log_callback
        self.locale = locale  # overrides microedition.locale, e.g. "de" or "fr" --
                               # different jars ship different locale resource packs

        self.classloader = None
        self.native_bridge = None
        self.jvm_engine = None
        self.midlet_obj = None
        self.midlet_class_name = None
        self.jar_path = None
        self.rms_dir = None

        self.canvases = []
        self.current_displayable = None
        # (commands and their listener are stored directly on each
        # Displayable's own fields dict now -- see add_command/
        # set_command_listener -- rather than in a lookup here)

        self.offscreen = None
        self.screen = None
        self.running = False
        self.exit_requested = False
        self.threads = []
        self.key_states = 0

        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # logging / error reporting
    # ------------------------------------------------------------------
    def log(self, level, text):
        if self.log_callback:
            try:
                self.log_callback(level, text)
                return
            except Exception:
                pass
        stream = sys.stderr if level in ("error", "stderr", "warn") else sys.stdout
        print(f"[{level}] {text}", file=stream)

    def engine_throw(self, class_name, message):
        self.jvm_engine.throw(class_name, message)

    # ------------------------------------------------------------------
    # loading & lifecycle
    # ------------------------------------------------------------------
    def load_jar(self, jar_path):
        self.jar_path = jar_path
        self.classloader = ClassLoader(jar_path)
        self.native_bridge = NativeBridge(self.classloader, self)
        self.jvm_engine = Engine(self.classloader, self.native_bridge)
        base = os.path.splitext(os.path.basename(jar_path))[0]
        self.rms_dir = os.path.join(os.path.dirname(os.path.abspath(jar_path)), ".j2me_rms", base)
        pygame.font.init()
        self.offscreen = pygame.Surface((self.width, self.height))
        self.offscreen.fill((255, 255, 255))

    def init_display(self):
        if self.screen is not None:
            return
        pygame.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((self.width * self.scale, self.height * self.scale))
        pygame.display.set_caption("J2ME Emulator")

    def rms_path(self, store_name):
        os.makedirs(self.rms_dir, exist_ok=True)
        safe = "".join(c for c in store_name if c.isalnum() or c in "-_") or "store"
        return os.path.join(self.rms_dir, safe + ".rms")

    def find_midlet_class(self):
        for name in self.classloader.list_class_names():
            cur = name
            seen = set()
            while cur and cur not in seen:
                seen.add(cur)
                if cur == "javax/microedition/midlet/MIDlet":
                    return name
                if self.classloader.has_class(cur):
                    cur = self.classloader.get_class(cur).super_name
                else:
                    break
        return None

    def start_midlet(self, class_name=None):
        self.init_display()  # must exist before startApp() can load/convert any Image
        class_name = class_name or self.find_midlet_class()
        if not class_name:
            raise RuntimeError("No class extending javax.microedition.midlet.MIDlet was found in this JAR.")
        self.midlet_class_name = class_name
        pygame.display.set_caption(f"J2ME Emulator - {class_name.replace('/', '.')}")
        self.midlet_obj = self.jvm_engine.new_instance(class_name)
        self._safe_call(self.jvm_engine.invoke_special, self.midlet_obj, class_name, "<init>", "()V", [])
        self._safe_call(self.jvm_engine.invoke_virtual, self.midlet_obj, class_name, "startApp", "()V", [])

    def _safe_call(self, fn, *fn_args, timeout=None):
        if timeout is None:
            self._lock.acquire()
        elif not self._lock.acquire(timeout=timeout):
            return None  # interpreter busy -- caller skips this frame/keystroke
        try:
            return fn(*fn_args)
        except JavaThrowable as exc:
            jo = exc.java_object
            cname = jo.class_name if isinstance(jo, JavaObject) else str(jo)
            msg = jo.fields.get("message") if isinstance(jo, JavaObject) else ""
            self.log("error", f"Uncaught {cname.replace('/', '.')}: {msg}")
        except Exception as exc:  # noqa: BLE001 - surface any interpreter bug without killing the loop
            call_stack = self.jvm_engine.call_stack_snapshot() if self.jvm_engine else []
            tb = traceback.format_exc(limit=6)
            self.log("error", f"{type(exc).__name__}: {exc} | "
                               f"Java call stack (innermost last): {call_stack}\n{tb}")
        finally:
            self._lock.release()
        return None

    # ------------------------------------------------------------------
    # host interface used by midp/natives.py
    # ------------------------------------------------------------------
    def register_canvas(self, obj):
        if obj not in self.canvases:
            self.canvases.append(obj)

    def set_current(self, displayable):
        self.current_displayable = displayable
        if displayable is not None:
            self.repaint(displayable)

    def repaint(self, canvas_obj, timeout=None):
        if canvas_obj is None:
            return
        if self.jvm_engine.is_instance_of(canvas_obj.class_name, "javax/microedition/lcdui/List"):
            self.native_bridge.render_list(canvas_obj, self.offscreen)
        elif self.jvm_engine.is_instance_of(canvas_obj.class_name, "javax/microedition/lcdui/Form"):
            self.native_bridge.render_form(canvas_obj, self.offscreen)
        elif self.jvm_engine.is_instance_of(canvas_obj.class_name, "javax/microedition/lcdui/Alert"):
            self.native_bridge.render_alert(canvas_obj, self.offscreen)
        elif self.jvm_engine.is_instance_of(canvas_obj.class_name, "javax/microedition/lcdui/TextBox"):
            self.native_bridge.render_textbox(canvas_obj, self.offscreen)
        else:
            gfx = self._graphics_for(canvas_obj)
            cname = canvas_obj.class_name
            # timeout matters here specifically: this is the one repaint()
            # branch that re-enters the interpreter (custom Canvas.paint()),
            # so it's the one that can contend with a MIDlet game-loop
            # Thread that's holding self._lock for its own run().
            self._safe_call(self.jvm_engine.invoke_virtual, canvas_obj, cname,
                             "paint", "(Ljavax/microedition/lcdui/Graphics;)V", [gfx], timeout=timeout)
            self.native_bridge.render_ticker_overlay(canvas_obj, self.offscreen)
        self.native_bridge.render_hotbar(canvas_obj, self.offscreen)

    def get_offscreen_graphics(self, canvas_obj):
        return self._graphics_for(canvas_obj)

    def _graphics_for(self, _canvas_obj):
        gfx = JavaObject("javax/microedition/lcdui/Graphics", {})
        gfx.native_companion = GraphicsSurface(self.offscreen, self.native_bridge._font_cache)
        return gfx

    def get_key_states(self):
        return self.key_states

    def start_thread(self, target_obj, java_thread_obj=None):
        def runner():
            if java_thread_obj is not None:
                self.native_bridge.set_current_thread(java_thread_obj)
            cname = target_obj.class_name if isinstance(target_obj, JavaObject) else "java/lang/Runnable"
            self._safe_call(self.jvm_engine.invoke_virtual, target_obj, cname, "run", "()V", [])

        t = threading.Thread(target=runner, daemon=True)
        self.threads.append(t)
        t.start()

    def request_exit(self):
        self.exit_requested = True

    def add_command(self, displayable, command_obj):
        # Stored on the object itself (not a global id()-keyed dict): J2ME
        # games routinely construct a fresh List/Form each time a screen is
        # shown, and once the old one is garbage-collected Python can reuse
        # its address for a new, unrelated object -- an id()-keyed lookup
        # would then silently return a stale listener from a totally
        # different screen. Keeping it on the object's own fields dict ties
        # its lifetime directly to the object, so there's nothing to leak
        # or collide.
        displayable.fields.setdefault("_commands", []).append(command_obj)

    def set_command_listener(self, displayable, listener_obj):
        displayable.fields["_command_listener"] = listener_obj

    def get_commands(self, displayable):
        return displayable.fields.get("_commands", [])

    def get_command_listener(self, displayable):
        return displayable.fields.get("_command_listener")

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    def run(self):
        self.init_display()
        clock = pygame.time.Clock()
        self.running = True

        # guarantee something is on screen even if the MIDlet never calls repaint()
        if self.current_displayable is None and self.canvases:
            self.set_current(self.canvases[0])

        while self.running and not self.exit_requested:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    self._handle_key(event.key, True)
                elif event.type == pygame.KEYUP:
                    self._handle_key(event.key, False)
                elif event.type == pygame.TEXTINPUT:
                    self._handle_text_input(event.text)

            if self.current_displayable is not None and (
                    self.force_continuous_repaint
                    or self.native_bridge.displayable_needs_animation(self.current_displayable)):
                self.repaint(self.current_displayable, timeout=_HOT_PATH_LOCK_TIMEOUT)

            # Non-blocking: if a MIDlet game-loop Thread currently holds
            # self._lock, just leave the previous frame on screen rather
            # than waiting -- flip()/tick() below must run every iteration
            # no matter what, or the window stops pumping OS messages.
            if self._lock.acquire(timeout=_HOT_PATH_LOCK_TIMEOUT):
                try:
                    if self.scale == 1:
                        self.screen.blit(self.offscreen, (0, 0))
                    else:
                        pygame.transform.scale(self.offscreen, self.screen.get_size(), self.screen)
                finally:
                    self._lock.release()
            pygame.display.flip()
            clock.tick(self.target_fps)

        if self.midlet_obj:
            self._safe_call(self.jvm_engine.invoke_virtual, self.midlet_obj, self.midlet_class_name,
                             "destroyApp", "(Z)V", [True])
        pygame.quit()

    def _handle_text_input(self, text):
        target = self.current_displayable or (self.canvases[0] if self.canvases else None)
        if target is None or not self.jvm_engine.is_instance_of(target.class_name, "javax/microedition/lcdui/TextBox"):
            return
        self.native_bridge.textbox_type(target, text)
        self.repaint(target, timeout=_HOT_PATH_LOCK_TIMEOUT)

    def _handle_key(self, pg_key, down):
        target = self.current_displayable or (self.canvases[0] if self.canvases else None)

        # TextBox steals the raw keyboard for free text entry -- it needs
        # keys (Backspace, letters, ...) that aren't part of the fixed MIDP
        # numeric-keypad mapping at all, so this has to run before that
        # mapping is even consulted. Enter/Escape are repurposed as the
        # left/right soft-key triggers (Q/W would otherwise collide with
        # typing the letters 'q'/'w').
        if target is not None and self.jvm_engine.is_instance_of(target.class_name, "javax/microedition/lcdui/TextBox"):
            if down:
                if pg_key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.native_bridge.fire_soft_key_command(target, K.KEY_SOFT_LEFT)
                elif pg_key == pygame.K_ESCAPE:
                    self.native_bridge.fire_soft_key_command(target, K.KEY_SOFT_RIGHT)
                else:
                    self.native_bridge.textbox_edit_key(target, pg_key)
                if self.current_displayable is not None:
                    self.repaint(self.current_displayable, timeout=_HOT_PATH_LOCK_TIMEOUT)
            return

        code = K.midp_keycode_for_pygame_key(pg_key)
        if code is None:
            return
        bit = _KEYSTATE_BITS.get(code, 0)
        if down:
            self.key_states |= bit
        else:
            self.key_states &= ~bit
        if target is None:
            return

        # Alert is an essentially-modal notice: any key dismisses it. If the
        # app registered custom Commands, the matching soft key routes to
        # those instead of the implicit DISMISS_COMMAND -- alert_dismiss
        # handles both cases and the "advance to the next Displayable" part.
        if self.jvm_engine.is_instance_of(target.class_name, "javax/microedition/lcdui/Alert"):
            if down:
                self.native_bridge.alert_dismiss(target, code)
                if self.current_displayable is not None:
                    self.repaint(self.current_displayable, timeout=_HOT_PATH_LOCK_TIMEOUT)
            return

        if down and code in (K.KEY_SOFT_LEFT, K.KEY_SOFT_RIGHT):
            if self.native_bridge.fire_soft_key_command(target, code):
                # commandAction may have switched screens (Display.setCurrent);
                # repaint whatever is actually current now, not the possibly
                # stale `target` we started this key event with.
                if self.current_displayable is not None:
                    self.repaint(self.current_displayable, timeout=_HOT_PATH_LOCK_TIMEOUT)
                return  # a registered Command handled it -- don't also
                        # deliver a raw keyPressed for the same press

        if self.jvm_engine.is_instance_of(target.class_name, "javax/microedition/lcdui/List"):
            if down:
                if code == K.KEY_UP:
                    self.native_bridge.list_navigate(target, -1)
                elif code == K.KEY_DOWN:
                    self.native_bridge.list_navigate(target, 1)
                elif code == K.KEY_FIRE:
                    self.native_bridge.list_fire(target)
                # list_fire()'s commandAction callback may itself have called
                # Display.setCurrent(...) to switch screens -- repaint whatever
                # is actually current now, not the (possibly stale) List we
                # started this key event with.
                if self.current_displayable is not None:
                    self.repaint(self.current_displayable, timeout=_HOT_PATH_LOCK_TIMEOUT)
            return
        if self.jvm_engine.is_instance_of(target.class_name, "javax/microedition/lcdui/Form"):
            if down:
                if code == K.KEY_UP:
                    self.native_bridge.form_navigate(target, -1)
                elif code == K.KEY_DOWN:
                    self.native_bridge.form_navigate(target, 1)
                elif code == K.KEY_LEFT:
                    self.native_bridge.form_adjust(target, -1)
                elif code == K.KEY_RIGHT:
                    self.native_bridge.form_adjust(target, 1)
                elif code == K.KEY_FIRE:
                    self.native_bridge.form_fire(target)
                if self.current_displayable is not None:
                    self.repaint(self.current_displayable, timeout=_HOT_PATH_LOCK_TIMEOUT)
            return
        method = "keyPressed" if down else "keyReleased"
        self._safe_call(self.jvm_engine.invoke_virtual, target, target.class_name, method, "(I)V", [code],
                         timeout=_HOT_PATH_LOCK_TIMEOUT)
