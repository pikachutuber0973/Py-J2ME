import datetime
import io
import math
import random
import struct
import threading
import time

import pygame

from jvm.machine import JavaObject, JavaArray, JavaThrowable
from midp import keys as K


def _s(v):
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, JavaObject):
        return v.fields.get("value", "")
    return str(v)


# Real MIDP javax.microedition.lcdui.Graphics anchor/style constants.
GFX_HCENTER, GFX_VCENTER, GFX_LEFT, GFX_RIGHT = 1, 2, 4, 8
GFX_TOP, GFX_BOTTOM, GFX_BASELINE = 16, 32, 64
GFX_SOLID, GFX_DOTTED = 0, 1

CHOICE_EXCLUSIVE, CHOICE_IMPLICIT, CHOICE_MULTIPLE, CHOICE_POPUP = 1, 2, 3, 4


ALERT_FOREVER = -2


_BACK_COMMAND_TYPES = {2, 3, 6, 7}


SUPERCLASS = {
    "javax/microedition/lcdui/game/GameCanvas": "javax/microedition/lcdui/Canvas",
    "javax/microedition/lcdui/game/Sprite": "javax/microedition/lcdui/game/Layer",
    "javax/microedition/lcdui/game/TiledLayer": "javax/microedition/lcdui/game/Layer",
    "javax/microedition/lcdui/game/Layer": "java/lang/Object",
    "javax/microedition/lcdui/game/LayerManager": "java/lang/Object",
    "java/io/ByteArrayInputStream": "java/io/InputStream",
    "java/io/DataInputStream": "java/io/InputStream",
    "java/io/InputStream": "java/lang/Object",
    "java/io/ByteArrayOutputStream": "java/io/OutputStream",
    "java/io/DataOutputStream": "java/io/OutputStream",
    "java/io/OutputStream": "java/lang/Object",
    "java/io/EOFException": "java/io/IOException",
    "javax/microedition/lcdui/Canvas": "javax/microedition/lcdui/Displayable",
    # Screen is the real common ancestor of every platform-rendered (i.e.
    # non-Canvas) high-level screen -- Displayable -> Screen -> {Form, List,
    # Alert, TextBox}. It adds no public API of its own; it exists purely so
    # `instanceof Screen` works for code that checks for it generically.
    "javax/microedition/lcdui/Screen": "javax/microedition/lcdui/Displayable",
    "javax/microedition/lcdui/Form": "javax/microedition/lcdui/Screen",
    "javax/microedition/lcdui/List": "javax/microedition/lcdui/Screen",
    "javax/microedition/lcdui/Alert": "javax/microedition/lcdui/Screen",
    "javax/microedition/lcdui/TextBox": "javax/microedition/lcdui/Screen",
    "javax/microedition/lcdui/Displayable": "java/lang/Object",
    "javax/microedition/lcdui/Display": "java/lang/Object",
    "javax/microedition/lcdui/Graphics": "java/lang/Object",
    "javax/microedition/lcdui/Image": "java/lang/Object",
    "javax/microedition/lcdui/Font": "java/lang/Object",
    "javax/microedition/lcdui/Command": "java/lang/Object",
    "javax/microedition/lcdui/AlertType": "java/lang/Object",
    "javax/microedition/lcdui/Ticker": "java/lang/Object",
    "javax/microedition/lcdui/ItemCommandListener": "java/lang/Object",
    "javax/microedition/lcdui/ItemStateListener": "java/lang/Object",
    "javax/microedition/lcdui/Choice": "java/lang/Object",
    "javax/microedition/midlet/MIDlet": "java/lang/Object",
    "java/lang/Thread": "java/lang/Object",
    "java/lang/String": "java/lang/Object",
    "java/lang/StringBuffer": "java/lang/Object",
    "java/lang/StringBuilder": "java/lang/Object",
    "java/io/PrintStream": "java/lang/Object",
    "java/util/Vector": "java/lang/Object",
    "java/util/Hashtable": "java/lang/Object",
    "java/util/Random": "java/lang/Object",
    "java/util/Timer": "java/lang/Object",
    "java/util/TimerTask": "java/lang/Object",
    "java/lang/Throwable": "java/lang/Object",
    "java/lang/Exception": "java/lang/Throwable",
    "java/lang/RuntimeException": "java/lang/Exception",
    "java/lang/IOException": "java/lang/Exception",
    "java/lang/InterruptedException": "java/lang/Exception",
    "java/lang/ClassNotFoundException": "java/lang/Exception",
    "java/lang/NullPointerException": "java/lang/RuntimeException",
    "java/lang/ArithmeticException": "java/lang/RuntimeException",
    "java/lang/ClassCastException": "java/lang/RuntimeException",
    "java/lang/NegativeArraySizeException": "java/lang/RuntimeException",
    "java/lang/IllegalArgumentException": "java/lang/RuntimeException",
    "java/lang/IllegalStateException": "java/lang/RuntimeException",
    "java/lang/IndexOutOfBoundsException": "java/lang/RuntimeException",
    "java/lang/ArrayIndexOutOfBoundsException": "java/lang/IndexOutOfBoundsException",
    "java/lang/StringIndexOutOfBoundsException": "java/lang/IndexOutOfBoundsException",
    "java/lang/NumberFormatException": "java/lang/IllegalArgumentException",
    "java/util/NoSuchElementException": "java/lang/RuntimeException",
}


class FontCache:

    SIZE_PX = {8: 14, 0: 18, 16: 24}  # SIZE_SMALL/MEDIUM/LARGE -> pixel size

    def __init__(self):
        pygame.font.init()
        self._cache = {}

    def get(self, font_obj):
        if font_obj is None:
            key = (0, 0)
        else:
            key = (font_obj.fields.get("style", 0), font_obj.fields.get("size", 0))
        if key not in self._cache:
            style, size = key
            px = self.SIZE_PX.get(size, 18)
            f = pygame.font.SysFont(None, px)
            f.set_bold(bool(style & 1))
            f.set_italic(bool(style & 2))
            self._cache[key] = f
        return self._cache[key]


class GraphicsSurface:
    def __init__(self, surface: pygame.Surface, font_cache):
        self.surface = surface
        self.color = (0, 0, 0)
        self.tx = 0
        self.ty = 0
        self.font_obj = None
        self._font_cache = font_cache
        surface.set_clip(None)

    def font(self):
        return self._font_cache.get(self.font_obj)


class NativeBridge:
    def __init__(self, classloader, host):
        self.cl = classloader
        self.host = host
        self.engine = None  # set by jvm.engine.Engine.__init__
        self.methods = {}
        self.methods_any = {}
        self.static_fields = {}
        self.superclass_map = dict(SUPERCLASS)
        self._display_singleton = None
        self._font_cache = FontCache()
        self._thread_local = threading.local()
        self._system_properties = dict(self._SYSTEM_PROPERTIES_DEFAULTS)
        override_locale = getattr(host, "locale", None)
        if override_locale:
            self._system_properties["microedition.locale"] = override_locale
        self._register_all()

    # ------------------------------------------------------------------
    # registration helpers
    # ------------------------------------------------------------------
    def m(self, class_name, name, desc, fn):
        self.methods[(class_name, name, desc)] = fn

    def m_any(self, class_name, name, fn):
        self.methods_any[(class_name, name)] = fn

    def _require(self, obj, what, context):
        if isinstance(obj, JavaObject) and obj.native_companion is not None:
            return obj.native_companion
        actual = type(obj).__name__ if obj is not None else "null"
        if obj is None:
            self.host.engine_throw("java/lang/NullPointerException", f"{context}: expected {what}, got null")
        else:
            self.host.engine_throw("java/lang/ClassCastException", f"{context}: expected {what}, got {actual}")

    def sf(self, class_name, name, value):
        self.static_fields[(class_name, name)] = value


    def call(self, class_name, name, desc, obj, args):
        cur = class_name
        seen = set()
        while cur and cur not in seen:
            seen.add(cur)
            fn = self.methods.get((cur, name, desc)) or self.methods_any.get((cur, name))
            if fn:
                try:
                    return fn(obj, args)
                except JavaThrowable:
                    raise
                except (AttributeError, TypeError, KeyError, IndexError, ValueError) as exc:
                    # Anything reaching here means a native handler received a
                    # receiver/argument of an unexpected type/shape -- most
                    # likely a genuine interpreter bug (e.g. a stack-accounting
                    # edge case) rather than a simple missing-guard, since
                    # every known Image/Graphics-typed argument site is
                    # already validated before this point. Converting it to a
                    # detailed log line (exactly which method, what types were
                    # actually involved, and which Java method was on the call
                    # stack at the time) plus a normal catchable exception is
                    # far more useful than a bare, undiagnosable crash -- and
                    # gives an exact lead the next time this happens.
                    arg_types = [type(a).__name__ for a in args]
                    call_stack = self.engine.call_stack_snapshot()
                    self.host.log("error", f"[native call bug] {class_name}.{name}{desc} "
                                            f"receiver={type(obj).__name__} args={arg_types}: "
                                            f"{type(exc).__name__}: {exc} | "
                                            f"Java call stack (innermost last): {call_stack}")
                    self.host.engine_throw("java/lang/RuntimeException",
                                            f"internal error in {name}{desc}: {exc}")
            cur = self.superclass_map.get(cur)
        return self._generic_fallback(class_name, name, desc, obj, args)

    def _generic_fallback(self, class_name, name, desc, obj, args):
        if name == "<init>":
            if args and isinstance(obj, JavaObject) and isinstance(args[0], (str,)):
                obj.fields["message"] = args[0]
            return None
        if name == "toString" and desc == "()Ljava/lang/String;":
            cname = obj.class_name if isinstance(obj, JavaObject) else class_name
            msg = obj.fields.get("message") if isinstance(obj, JavaObject) else None
            return f"{cname.replace('/', '.')}: {msg}" if msg else cname.replace("/", ".")
        if name == "getMessage" and isinstance(obj, JavaObject):
            return obj.fields.get("message")
        if name == "equals":
            return 1 if obj is (args[0] if args else None) else 0
        if name == "hashCode":
            return id(obj) & 0x7FFFFFFF
        if name == "printStackTrace":
            msg = obj.fields.get("message") if isinstance(obj, JavaObject) else ""
            self.host.log("error", f"Exception: {class_name.replace('/', '.')}: {msg}")
            return None
        self.host.log("warn", f"[unimplemented] {class_name}.{name}{desc}")
        # desc is the full method descriptor ("(I)Z", "()V", ...); the
        # return type is whatever follows the closing ')'.
        return self._default_for_type(desc.rsplit(")", 1)[-1])

    def _default_for_type(self, type_desc):
        if type_desc in ("I", "S", "B", "C", "Z", "J"):
            return 0
        if type_desc in ("F", "D"):
            return 0.0
        return None

    def ensure_static_init(self, class_name):
        pass  # native classes have no bytecode <clinit> to run

    def get_static_field(self, class_name, name, desc):
        if (class_name, name) in self.static_fields:
            v = self.static_fields[(class_name, name)]
            return v() if callable(v) else v
        self.host.log("warn", f"[unimplemented static field] {class_name}.{name}")
        return self._default_for_type(desc)

    def put_static_field(self, class_name, name, desc, value):
        self.static_fields[(class_name, name)] = value

    def get_instance_field(self, obj, owner, name, desc):
        return self._default_for_type(desc)

    def put_instance_field(self, obj, owner, name, desc, value):
        pass

    def is_instance_of(self, class_name, target):
        cur = class_name
        seen = set()
        while cur and cur not in seen:
            if cur == target:
                return True
            seen.add(cur)
            cur = self.superclass_map.get(cur)
        return target == "java/lang/Object"

    def instantiate(self, obj, boundary_class):
        if boundary_class == "java/lang/StringBuffer" or boundary_class == "java/lang/StringBuilder":
            obj.native_companion = []
        elif boundary_class == "java/lang/Thread":
            obj.native_companion = {"target": None}
        elif boundary_class == "java/util/Vector":
            obj.native_companion = []
        elif boundary_class == "java/util/Hashtable":
            obj.native_companion = {}
        elif boundary_class == "java/util/Random":
            obj.native_companion = random.Random()
        elif boundary_class == "java/util/Timer":
            obj.native_companion = _TimerState()
        elif boundary_class == "java/util/TimerTask":
            obj.native_companion = {"cancelled": False}
        elif boundary_class in ("javax/microedition/lcdui/Canvas", "javax/microedition/lcdui/game/GameCanvas"):
            obj.native_companion = {}
            self.host.register_canvas(obj)
        elif boundary_class == "javax/microedition/lcdui/Form":
            obj.native_companion = _FormState()
        elif boundary_class == "javax/microedition/lcdui/List":
            obj.native_companion = _ListState()
        elif boundary_class == "javax/microedition/lcdui/ChoiceGroup":
            obj.native_companion = _ListState()  # same shape: list_type/items/selected/cursor
        elif boundary_class == "javax/microedition/lcdui/Alert":
            obj.native_companion = _AlertState()
        elif boundary_class == "javax/microedition/lcdui/game/Sprite":
            obj.native_companion = _SpriteState()
        elif boundary_class == "javax/microedition/lcdui/game/TiledLayer":
            obj.native_companion = _TiledLayerState()
        elif boundary_class == "javax/microedition/lcdui/game/LayerManager":
            obj.native_companion = _LayerManagerState()
        elif boundary_class in ("java/io/ByteArrayInputStream", "java/io/DataInputStream"):
            obj.native_companion = _ByteStream(b"")
        elif boundary_class in ("java/io/ByteArrayOutputStream", "java/io/DataOutputStream"):
            obj.native_companion = _ByteSink()

    # registration of every native method/field

    def _register_all(self):
        self._reg_object()
        self._reg_string()
        self._reg_stringbuffer()
        self._reg_system()
        self._reg_math()
        self._reg_wrappers()
        self._reg_collections()
        self._reg_thread()
        self._reg_midlet()
        self._reg_display()
        self._reg_canvas()
        self._reg_graphics()
        self._reg_image()
        self._reg_font()
        self._reg_command_and_form()
        self._reg_alert_type()
        self._reg_alert()
        self._reg_ticker()
        self._reg_textbox()
        self._reg_recordstore()
        self._reg_nokia_ui()
        self._reg_game_api()
        self._reg_io()
        self._reg_timer()

    # ---- java/lang/Object -------------------------------------------------
    def _reg_object(self):
        self.m("java/lang/Object", "<init>", "()V", lambda o, a: None)
        self.m("java/lang/Object", "getClass", "()Ljava/lang/Class;",
               lambda o, a: self.engine.class_object_for(o.class_name if isinstance(o, JavaObject) else "?"))
        # NOTE on wait()/notify(): a real blocking wait() would need to release
        # the interpreter's global cross-thread lock while parked, which isn't
        # safe to do generically from here. Instead, wait() returns after a
        # short bounded time (no-arg) or after its given timeout (timed form),
        # like Thread.sleep. This is spec-legal (Java explicitly permits
        # "spurious wakeups" from wait(), and correct code re-checks its
        # condition in a loop) and matches the extremely common
        # synchronized(this){ wait(period); } game-loop pacing pattern.
        # notify()/notifyAll() are therefore no-ops -- nothing to wake since
        # nothing blocks indefinitely.
        self.m("java/lang/Object", "wait", "()V", lambda o, a: self._thread_sleep(100))
        self.m("java/lang/Object", "wait", "(J)V", lambda o, a: self._thread_sleep(a[0] if a[0] > 0 else 100))
        self.m("java/lang/Object", "wait", "(JI)V", lambda o, a: self._thread_sleep(a[0] if a[0] > 0 else 100))
        self.m("java/lang/Object", "notify", "()V", lambda o, a: None)
        self.m("java/lang/Object", "notifyAll", "()V", lambda o, a: None)

    # ---- java/lang/String ---------------------------------------------------
    def _reg_string(self):
        C = "java/lang/String"
        self.m(C, "<init>", "()V", lambda o, a: o.fields.__setitem__("value", ""))
        self.m(C, "<init>", "(Ljava/lang/String;)V", lambda o, a: o.fields.__setitem__("value", _s(a[0])))
        self.m(C, "<init>", "([C)V", lambda o, a: o.fields.__setitem__("value", "".join(chr(c) for c in a[0].values)))
        self.m(C, "length", "()I", lambda o, a: len(_s(o)))
        self.m(C, "charAt", "(I)C", lambda o, a: self._charat(o, a))
        self.m(C, "equals", "(Ljava/lang/Object;)Z", lambda o, a: 1 if _s(o) == _s(a[0]) else 0)
        self.m(C, "equalsIgnoreCase", "(Ljava/lang/String;)Z",
               lambda o, a: 1 if _s(o).lower() == (_s(a[0]) or "").lower() else 0)
        self.m(C, "concat", "(Ljava/lang/String;)Ljava/lang/String;", lambda o, a: _s(o) + _s(a[0]))
        self.m(C, "toCharArray", "()[C", lambda o, a: JavaArray("C", [ord(c) for c in _s(o)]))
        self.m(C, "trim", "()Ljava/lang/String;", lambda o, a: _s(o).strip())
        self.m(C, "toUpperCase", "()Ljava/lang/String;", lambda o, a: _s(o).upper())
        self.m(C, "toLowerCase", "()Ljava/lang/String;", lambda o, a: _s(o).lower())
        self.m(C, "hashCode", "()I", lambda o, a: self._javastr_hash(_s(o)))
        self.m(C, "compareTo", "(Ljava/lang/String;)I", lambda o, a: self._cmp(_s(o), _s(a[0])))
        self.m(C, "startsWith", "(Ljava/lang/String;)Z", lambda o, a: 1 if _s(o).startswith(_s(a[0])) else 0)
        self.m(C, "endsWith", "(Ljava/lang/String;)Z", lambda o, a: 1 if _s(o).endswith(_s(a[0])) else 0)
        self.m(C, "toString", "()Ljava/lang/String;", lambda o, a: _s(o))
        self.m_any(C, "indexOf", lambda o, a: self._indexof(o, a))
        self.m_any(C, "substring", lambda o, a: self._substring(o, a))
        self.m_any(C, "valueOf", lambda o, a: self._string_valueof(a))
        self.m(C, "valueOf", "(Z)Ljava/lang/String;", lambda o, a: "true" if a[0] else "false")
        self.m_any(C, "replace", lambda o, a: _s(o).replace(chr(a[0]) if isinstance(a[0], int) else _s(a[0]),
                                                             chr(a[1]) if isinstance(a[1], int) else _s(a[1])))

    def _charat(self, o, a):
        s = _s(o); i = a[0]
        if i < 0 or i >= len(s):
            self.host.engine_throw("java/lang/StringIndexOutOfBoundsException", str(i))
        return ord(s[i])

    def _cmp(self, a, b):
        if a == b:
            return 0
        return -1 if a < b else 1

    def _javastr_hash(self, s):
        h = 0
        for c in s:
            h = (31 * h + ord(c)) & 0xFFFFFFFF
        return h - 0x100000000 if h & 0x80000000 else h

    def _indexof(self, o, a):
        s = _s(o)
        if isinstance(a[0], int) and not isinstance(a[0], bool):
            needle = chr(a[0])
        else:
            needle = _s(a[0])
        start = a[1] if len(a) > 1 else 0
        return s.find(needle, start)

    def _substring(self, o, a):
        s = _s(o)
        begin = a[0]
        end = a[1] if len(a) > 1 else len(s)
        if begin < 0 or end > len(s) or begin > end:
            self.host.engine_throw("java/lang/StringIndexOutOfBoundsException", f"{begin},{end}")
        return s[begin:end]

    def _string_valueof(self, a):
        v = a[0]
        if v is None:
            return "null"
        if isinstance(v, JavaArray):
            return "".join(chr(c) for c in v.values)
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, float):
            return _format_java_float(v)
        return _s(v) if isinstance(v, (str, JavaObject)) else str(v)

    # ---- java/lang/StringBuffer & StringBuilder -----------------------------
    def _reg_stringbuffer(self):
        for C in ("java/lang/StringBuffer", "java/lang/StringBuilder"):
            self.m(C, "<init>", "()V", lambda o, a: None)
            self.m(C, "<init>", "(I)V", lambda o, a: None)
            self.m(C, "<init>", "(Ljava/lang/String;)V", lambda o, a: o.native_companion.append(_s(a[0])))
            self.m_any(C, "append", lambda o, a: self._sb_append(o, a))
            self.m(C, "append", "(Z)Ljava/lang/StringBuffer;", lambda o, a: self._sb_append_bool(o, a))
            self.m(C, "append", "(Z)Ljava/lang/StringBuilder;", lambda o, a: self._sb_append_bool(o, a))
            self.m(C, "toString", "()Ljava/lang/String;", lambda o, a: "".join(o.native_companion))
            self.m(C, "length", "()I", lambda o, a: len("".join(o.native_companion)))
            self.m(C, "charAt", "(I)C", lambda o, a: ord("".join(o.native_companion)[a[0]]))
            self.m(C, "setLength", "(I)V", lambda o, a: self._sb_setlength(o, a))
            self.m(C, "reverse", "()Ljava/lang/StringBuilder;", lambda o, a: self._sb_reverse(o))
            self.m(C, "insert", "(ILjava/lang/String;)Ljava/lang/StringBuffer;", lambda o, a: self._sb_insert(o, a))
            self.m(C, "deleteCharAt", "(I)Ljava/lang/StringBuffer;", lambda o, a: self._sb_delete(o, a))

    def _sb_append(self, o, a):
        v = a[0]
        if isinstance(v, JavaArray):
            v = "".join(chr(c) for c in v.values)
        elif isinstance(v, bool):
            v = "true" if v else "false"
        elif isinstance(v, float):
            v = _format_java_float(v)
        elif isinstance(v, int):
            v = str(v)
        elif v is None:
            v = "null"
        else:
            v = _s(v)
        o.native_companion.append(v)
        return o

    def _sb_append_bool(self, o, a):
        o.native_companion.append("true" if a[0] else "false")
        return o

    def _sb_setlength(self, o, a):
        s = "".join(o.native_companion)
        n = a[0]
        s = (s[:n]).ljust(n, "\0") if n > len(s) else s[:n]
        o.native_companion.clear()
        o.native_companion.append(s)

    def _sb_reverse(self, o):
        s = "".join(o.native_companion)[::-1]
        o.native_companion.clear()
        o.native_companion.append(s)
        return o

    def _sb_insert(self, o, a):
        idx, val = a[0], _s(a[1]) if not isinstance(a[1], (int, float, bool)) else str(a[1])
        s = "".join(o.native_companion)
        s = s[:idx] + val + s[idx:]
        o.native_companion.clear()
        o.native_companion.append(s)
        return o

    def _sb_delete(self, o, a):
        idx = a[0]
        s = "".join(o.native_companion)
        s = s[:idx] + s[idx + 1:]
        o.native_companion.clear()
        o.native_companion.append(s)
        return o

    # ---- java/lang/System / java/io/PrintStream -----------------------------
    def _reg_system(self):
        C = "java/lang/System"
        out = JavaObject("java/io/PrintStream", {"which": "out"})
        err = JavaObject("java/io/PrintStream", {"which": "err"})
        self.sf(C, "out", out)
        self.sf(C, "err", err)
        self.m(C, "currentTimeMillis", "()J", lambda o, a: int(time.time() * 1000))
        self.m(C, "arraycopy", "(Ljava/lang/Object;ILjava/lang/Object;II)V", lambda o, a: self._arraycopy(a))
        self.m(C, "exit", "(I)V", lambda o, a: self.host.request_exit())
        self.m(C, "gc", "()V", lambda o, a: None)
        self.m(C, "getProperty", "(Ljava/lang/String;)Ljava/lang/String;", lambda o, a: self._get_property(_s(a[0])))

        P = "java/io/PrintStream"
        self.m_any(P, "println", lambda o, a: self._println(o, a))
        self.m_any(P, "print", lambda o, a: self._println(o, a, newline=False))

    _SYSTEM_PROPERTIES_DEFAULTS = {
        "microedition.platform": "Nokia6230i/05.51",
        "microedition.configuration": "CLDC-1.1",
        "microedition.profiles": "MIDP-2.0",
        "microedition.locale": "en-US",
        "microedition.encoding": "ISO-8859-1",
        "microedition.hostname": "localhost",
        "microedition.commports": "",
        "device.model": "Nokia",
        "microedition.jtwi.version": "1.0",
    }

    def _get_property(self, key):
        if key in self._system_properties:
            return self._system_properties[key]
        self.host.log("warn", f"[unimplemented system property] {key}")
        return None

    def _println(self, o, a, newline=True):
        text = self._string_valueof(a) if a else ""
        which = o.fields.get("which", "out") if isinstance(o, JavaObject) else "out"
        self.host.log("stderr" if which == "err" else "stdout", text)
        return None

    def _arraycopy(self, a):
        src, srcPos, dst, dstPos, length = a
        dst.values[dstPos:dstPos + length] = src.values[srcPos:srcPos + length]

    # ---- java/lang/Math -----------------------------------------------------
    def _reg_math(self):
        C = "java/lang/Math"
        self.m(C, "abs", "(I)I", lambda o, a: abs(a[0]))
        self.m(C, "abs", "(J)J", lambda o, a: abs(a[0]))
        self.m(C, "abs", "(F)F", lambda o, a: abs(a[0]))
        self.m(C, "abs", "(D)D", lambda o, a: abs(a[0]))
        self.m(C, "max", "(II)I", lambda o, a: max(a))
        self.m(C, "max", "(JJ)J", lambda o, a: max(a))
        self.m(C, "max", "(FF)F", lambda o, a: max(a))
        self.m(C, "max", "(DD)D", lambda o, a: max(a))
        self.m(C, "min", "(II)I", lambda o, a: min(a))
        self.m(C, "min", "(JJ)J", lambda o, a: min(a))
        self.m(C, "min", "(FF)F", lambda o, a: min(a))
        self.m(C, "min", "(DD)D", lambda o, a: min(a))
        self.m(C, "sqrt", "(D)D", lambda o, a: math.sqrt(a[0]) if a[0] >= 0 else float("nan"))
        self.m(C, "sin", "(D)D", lambda o, a: math.sin(a[0]))
        self.m(C, "cos", "(D)D", lambda o, a: math.cos(a[0]))
        self.m(C, "tan", "(D)D", lambda o, a: math.tan(a[0]))
        self.m(C, "atan2", "(DD)D", lambda o, a: math.atan2(a[0], a[1]))
        self.m(C, "pow", "(DD)D", lambda o, a: math.pow(a[0], a[1]))
        self.m(C, "floor", "(D)D", lambda o, a: math.floor(a[0]))
        self.m(C, "ceil", "(D)D", lambda o, a: math.ceil(a[0]))
        self.m(C, "round", "(F)I", lambda o, a: math.floor(a[0] + 0.5))
        self.m(C, "round", "(D)J", lambda o, a: math.floor(a[0] + 0.5))
        self.m(C, "random", "()D", lambda o, a: random.random())

    # ---- wrapper classes (Integer/Long/Float/Double/Character/Boolean) -----
    def _reg_wrappers(self):
        RT = "java/lang/Runtime"
        self.m(RT, "getRuntime", "()Ljava/lang/Runtime;", lambda o, a: self._get_runtime())
        self.m(RT, "totalMemory", "()J", lambda o, a: 16 * 1024 * 1024)
        self.m(RT, "freeMemory", "()J", lambda o, a: 8 * 1024 * 1024)
        self.m(RT, "gc", "()V", lambda o, a: None)
        self.m(RT, "exit", "(I)V", lambda o, a: self.host.request_exit())
        self.superclass_map[RT] = "java/lang/Object"

        self._reg_wrapper_type("java/lang/Integer", "I", "intValue", int, self._parse_int)
        self._reg_wrapper_type("java/lang/Long", "J", "longValue", int, lambda s: int(_s(s)))
        self._reg_wrapper_type("java/lang/Float", "F", "floatValue", float, lambda s: float(_s(s)))
        self._reg_wrapper_type("java/lang/Double", "D", "doubleValue", float, lambda s: float(_s(s)))
        self._reg_wrapper_type("java/lang/Boolean", "Z", "booleanValue", bool, lambda s: 1 if _s(s) == "true" else 0)
        self._reg_wrapper_type("java/lang/Character", "C", "charValue", int, lambda s: ord(_s(s)[0]))

        self.m("java/lang/Integer", "parseInt", "(Ljava/lang/String;)I", lambda o, a: self._parse_int(a[0]))
        self.m("java/lang/Integer", "toString", "(I)Ljava/lang/String;", lambda o, a: str(a[0]))
        self.m("java/lang/Long", "parseLong", "(Ljava/lang/String;)J", lambda o, a: int(_s(a[0])))
        self.m("java/lang/Long", "toString", "(J)Ljava/lang/String;", lambda o, a: str(a[0]))
        self.m("java/lang/Float", "parseFloat", "(Ljava/lang/String;)F", lambda o, a: float(_s(a[0])))
        self.m("java/lang/Float", "toString", "(F)Ljava/lang/String;", lambda o, a: _format_java_float(a[0]))
        self.m("java/lang/Double", "parseDouble", "(Ljava/lang/String;)D", lambda o, a: float(_s(a[0])))
        self.m("java/lang/Double", "toString", "(D)Ljava/lang/String;", lambda o, a: _format_java_float(a[0]))
        self.m("java/lang/Integer", "valueOf", "(Ljava/lang/String;)Ljava/lang/Integer;",
               lambda o, a: self._box("java/lang/Integer", self._parse_int(a[0])))
        self.m("java/lang/Long", "valueOf", "(Ljava/lang/String;)Ljava/lang/Long;",
               lambda o, a: self._box("java/lang/Long", int(_s(a[0]))))
        self.sf("java/lang/Integer", "MIN_VALUE", -0x80000000); self.sf("java/lang/Integer", "MAX_VALUE", 0x7FFFFFFF)
        self.sf("java/lang/Boolean", "TRUE", lambda: self._box("java/lang/Boolean", 1))
        self.sf("java/lang/Boolean", "FALSE", lambda: self._box("java/lang/Boolean", 0))
        self.m("java/lang/Character", "isDigit", "(C)Z", lambda o, a: 1 if chr(a[0]).isdigit() else 0)
        self.m("java/lang/Character", "isLetter", "(C)Z", lambda o, a: 1 if chr(a[0]).isalpha() else 0)
        self.m("java/lang/Character", "isWhitespace", "(C)Z", lambda o, a: 1 if chr(a[0]).isspace() else 0)
        self.m("java/lang/Character", "toUpperCase", "(C)C", lambda o, a: ord(chr(a[0]).upper()))
        self.m("java/lang/Character", "toLowerCase", "(C)C", lambda o, a: ord(chr(a[0]).lower()))

    def _reg_wrapper_type(self, class_name, prim_desc, unbox_name, py_type, parse_fn):
        self.m(class_name, "<init>", f"({prim_desc})V", lambda o, a: o.fields.__setitem__("value", a[0]))
        self.m(class_name, "<init>", "(Ljava/lang/String;)V", lambda o, a: o.fields.__setitem__("value", parse_fn(a[0])))
        self.m(class_name, unbox_name, f"(){prim_desc}", lambda o, a: o.fields.get("value", py_type()))
        self.m(class_name, "toString", "()Ljava/lang/String;", lambda o, a: self._string_valueof([o.fields.get("value")]))
        self.m(class_name, "equals", "(Ljava/lang/Object;)Z",
               lambda o, a: 1 if isinstance(a[0], JavaObject) and a[0].fields.get("value") == o.fields.get("value") else 0)
        self.m(class_name, "hashCode", "()I", lambda o, a: hash(o.fields.get("value")) & 0x7FFFFFFF)
        self.m(class_name, "valueOf", f"({prim_desc})L{class_name};", lambda o, a: self._box(class_name, a[0]))
        self.superclass_map[class_name] = "java/lang/Object"

    def _box(self, class_name, value):
        return JavaObject(class_name, {"value": value})

    def _get_runtime(self):
        if not hasattr(self, "_runtime_singleton"):
            self._runtime_singleton = JavaObject("java/lang/Runtime", {})
        return self._runtime_singleton

    def _parse_int(self, s):
        try:
            return int(_s(s))
        except (ValueError, TypeError):
            self.host.engine_throw("java/lang/NumberFormatException", str(s))

    # ---- java/util collections ----------------------------------------------
    def _reg_collections(self):
        V = "java/util/Vector"
        self.m(V, "<init>", "()V", lambda o, a: None)
        self.m(V, "<init>", "(I)V", lambda o, a: None)
        self.m(V, "<init>", "(II)V", lambda o, a: None)
        self.m(V, "addElement", "(Ljava/lang/Object;)V", lambda o, a: o.native_companion.append(a[0]))
        self.m(V, "elementAt", "(I)Ljava/lang/Object;", lambda o, a: self._vec_get(o, a[0]))
        self.m(V, "firstElement", "()Ljava/lang/Object;", lambda o, a: self._vec_first(o))
        self.m(V, "lastElement", "()Ljava/lang/Object;", lambda o, a: self._vec_last(o))
        self.m(V, "size", "()I", lambda o, a: len(o.native_companion))
        self.m(V, "isEmpty", "()Z", lambda o, a: 1 if not o.native_companion else 0)
        self.m(V, "removeElementAt", "(I)V", lambda o, a: self._vec_remove_at(o, a[0]))
        self.m(V, "removeElement", "(Ljava/lang/Object;)Z", lambda o, a: self._vec_remove(o, a))
        self.m(V, "insertElementAt", "(Ljava/lang/Object;I)V", lambda o, a: o.native_companion.insert(a[1], a[0]))
        self.m(V, "setElementAt", "(Ljava/lang/Object;I)V", lambda o, a: o.native_companion.__setitem__(a[1], a[0]))
        self.m(V, "removeAllElements", "()V", lambda o, a: o.native_companion.clear())
        self.m(V, "contains", "(Ljava/lang/Object;)Z", lambda o, a: 1 if a[0] in o.native_companion else 0)
        self.m(V, "indexOf", "(Ljava/lang/Object;)I", lambda o, a: self._vec_indexof(o, a[0], 0))
        self.m(V, "indexOf", "(Ljava/lang/Object;I)I", lambda o, a: self._vec_indexof(o, a[0], a[1]))
        self.m(V, "copyInto", "([Ljava/lang/Object;)V", lambda o, a: self._vec_copy_into(o, a[0]))
        self.m(V, "elements", "()Ljava/util/Enumeration;", lambda o, a: self._make_enumeration(list(o.native_companion)))

        H = "java/util/Hashtable"
        self.m(H, "<init>", "()V", lambda o, a: None)
        self.m(H, "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", lambda o, a: self._ht_put(o, a))
        self.m(H, "get", "(Ljava/lang/Object;)Ljava/lang/Object;", lambda o, a: o.native_companion.get(_hkey(a[0])))
        self.m(H, "containsKey", "(Ljava/lang/Object;)Z", lambda o, a: 1 if _hkey(a[0]) in o.native_companion else 0)
        self.m(H, "remove", "(Ljava/lang/Object;)Ljava/lang/Object;", lambda o, a: o.native_companion.pop(_hkey(a[0]), None))
        self.m(H, "size", "()I", lambda o, a: len(o.native_companion))
        self.m(H, "isEmpty", "()Z", lambda o, a: 1 if not o.native_companion else 0)
        self.m(H, "keys", "()Ljava/util/Enumeration;", lambda o, a: self._make_enumeration(list(o.native_companion.keys())))
        self.m(H, "elements", "()Ljava/util/Enumeration;", lambda o, a: self._make_enumeration(list(o.native_companion.values())))

        E = "java/util/Enumeration"
        self.m(E, "hasMoreElements", "()Z", lambda o, a: 1 if o.native_companion["pos"] < len(o.native_companion["items"]) else 0)
        self.m(E, "nextElement", "()Ljava/lang/Object;", lambda o, a: self._enum_next(o))
        self.superclass_map[E] = "java/lang/Object"

        R = "java/util/Random"
        self.m(R, "<init>", "()V", lambda o, a: None)
        self.m(R, "<init>", "(J)V", lambda o, a: o.native_companion.seed(a[0]))
        self.m(R, "nextInt", "()I", lambda o, a: o.native_companion.randint(-2**31, 2**31 - 1))
        self.m(R, "nextInt", "(I)I", lambda o, a: o.native_companion.randrange(a[0]))
        self.m(R, "nextBoolean", "()Z", lambda o, a: o.native_companion.randint(0, 1))
        self.m(R, "nextFloat", "()F", lambda o, a: o.native_companion.random())
        self.m(R, "nextDouble", "()D", lambda o, a: o.native_companion.random())
        self.m(R, "nextLong", "()J", lambda o, a: o.native_companion.randint(-2**63, 2**63 - 1))
        self.m(R, "setSeed", "(J)V", lambda o, a: o.native_companion.seed(a[0]))

    def _vec_get(self, o, i):
        if i < 0 or i >= len(o.native_companion):
            self.host.engine_throw("java/lang/ArrayIndexOutOfBoundsException", str(i))
        return o.native_companion[i]

    def _vec_first(self, o):
        if not o.native_companion:
            self.host.engine_throw("java/util/NoSuchElementException", "")
        return o.native_companion[0]

    def _vec_last(self, o):
        if not o.native_companion:
            self.host.engine_throw("java/util/NoSuchElementException", "")
        return o.native_companion[-1]

    def _vec_indexof(self, o, item, start):
        try:
            return o.native_companion.index(item, start)
        except ValueError:
            return -1

    def _vec_copy_into(self, o, dst_array):
        for i, v in enumerate(o.native_companion):
            if i < len(dst_array.values):
                dst_array.values[i] = v

    def _make_enumeration(self, items):
        e = JavaObject("java/util/Enumeration", {})
        e.native_companion = {"items": items, "pos": 0}
        return e

    def _enum_next(self, o):
        nc = o.native_companion
        if nc["pos"] >= len(nc["items"]):
            self.host.engine_throw("java/util/NoSuchElementException", "")
        v = nc["items"][nc["pos"]]
        nc["pos"] += 1
        return v

    def _vec_remove_at(self, o, i):
        if i < 0 or i >= len(o.native_companion):
            self.host.engine_throw("java/lang/ArrayIndexOutOfBoundsException", str(i))
        o.native_companion.pop(i)

    def _vec_remove(self, o, a):
        try:
            o.native_companion.remove(a[0]); return 1
        except ValueError:
            return 0

    def _ht_put(self, o, a):
        k, v = _hkey(a[0]), a[1]
        old = o.native_companion.get(k)
        o.native_companion[k] = v
        return old

    # ---- java/lang/Thread -----------------------------------------------------
    def _reg_thread(self):
        C = "java/lang/Thread"
        self.m(C, "<init>", "()V", lambda o, a: o.native_companion.__setitem__("target", o))
        self.m(C, "<init>", "(Ljava/lang/Runnable;)V", lambda o, a: o.native_companion.__setitem__("target", a[0]))
        self.m(C, "start", "()V", lambda o, a: self.host.start_thread(o.native_companion["target"], o))
        self.m(C, "run", "()V", lambda o, a: None)
        self.m(C, "setPriority", "(I)V", lambda o, a: None)
        self.m(C, "setName", "(Ljava/lang/String;)V", lambda o, a: o.native_companion.__setitem__("name", _s(a[0])))
        self.m(C, "getName", "()Ljava/lang/String;", lambda o, a: o.native_companion.get("name", "Thread"))
        self.m(C, "sleep", "(J)V", lambda o, a: self._thread_sleep(a[0]))
        self.m(C, "yield", "()V", lambda o, a: time.sleep(0))
        self.m(C, "currentThread", "()Ljava/lang/Thread;", lambda o, a: self._current_thread_obj())
        self.m(C, "interrupt", "()V", lambda o, a: o.native_companion.__setitem__("interrupted", True))
        self.m(C, "isInterrupted", "()Z", lambda o, a: 1 if o.native_companion.get("interrupted") else 0)
        self.m(C, "interrupted", "()Z", lambda o, a: self._thread_interrupted_static())
        self.m(C, "isAlive", "()Z", lambda o, a: 1 if o.native_companion.get("alive") else 0)

    def set_current_thread(self, thread_obj):
        self._thread_local.java_thread = thread_obj

    def _current_thread_obj(self):
        cur = getattr(self._thread_local, "java_thread", None)
        if cur is not None:
            return cur
        if not hasattr(self, "_main_thread_obj"):
            self._main_thread_obj = JavaObject("java/lang/Thread", {})
            self._main_thread_obj.native_companion = {"target": None, "interrupted": False, "name": "main"}
        return self._main_thread_obj

    def _thread_interrupted_static(self):
        cur = self._current_thread_obj()
        was = cur.native_companion.get("interrupted", False)
        cur.native_companion["interrupted"] = False
        return 1 if was else 0

    def _thread_sleep(self, millis):
        cur = getattr(self._thread_local, "java_thread", None)
        remaining = max(0, millis) / 1000.0
        step = 0.02
        while remaining > 0:
            if cur is not None and cur.native_companion.get("interrupted"):
                cur.native_companion["interrupted"] = False
                self.host.engine_throw("java/lang/InterruptedException", "")
            chunk = min(step, remaining)
            time.sleep(chunk)
            remaining -= chunk
        if cur is not None and cur.native_companion.get("interrupted"):
            cur.native_companion["interrupted"] = False
            self.host.engine_throw("java/lang/InterruptedException", "")

    # ---- java/util/Timer + TimerTask -------------------------------------------
    def _reg_timer(self):
        T = "java/util/Timer"
        self.m(T, "<init>", "()V", lambda o, a: None)
        self.m(T, "<init>", "(Z)V", lambda o, a: None)
        self.m(T, "schedule", "(Ljava/util/TimerTask;J)V", lambda o, a: self._timer_schedule(o, a[0], a[1], None))
        self.m(T, "schedule", "(Ljava/util/TimerTask;JJ)V", lambda o, a: self._timer_schedule(o, a[0], a[1], a[2]))
        self.m(T, "schedule", "(Ljava/util/TimerTask;Ljava/util/Date;)V",
               lambda o, a: self._timer_schedule_at(o, a[0], a[1], None))
        self.m(T, "schedule", "(Ljava/util/TimerTask;Ljava/util/Date;J)V",
               lambda o, a: self._timer_schedule_at(o, a[0], a[1], a[2]))
        self.m(T, "scheduleAtFixedRate", "(Ljava/util/TimerTask;JJ)V",
               lambda o, a: self._timer_schedule(o, a[0], a[1], a[2]))
        self.m(T, "scheduleAtFixedRate", "(Ljava/util/TimerTask;Ljava/util/Date;J)V",
               lambda o, a: self._timer_schedule_at(o, a[0], a[1], a[2]))
        self.m(T, "cancel", "()V", lambda o, a: setattr(o.native_companion, "cancelled", True))

        TT = "java/util/TimerTask"
        self.m(TT, "<init>", "()V", lambda o, a: None)
        self.m(TT, "cancel", "()Z", lambda o, a: self._timertask_cancel(o))
        self.m(TT, "run", "()V", lambda o, a: None)
        self.m(TT, "scheduledExecutionTime", "()J", lambda o, a: int(time.time() * 1000))

        DT = "java/util/Date"
        self.m(DT, "<init>", "()V", lambda o, a: o.fields.__setitem__("time", int(time.time() * 1000)))
        self.m(DT, "<init>", "(J)V", lambda o, a: o.fields.__setitem__("time", a[0]))
        self.m(DT, "getTime", "()J", lambda o, a: o.fields.get("time", 0))
        self.m(DT, "setTime", "(J)V", lambda o, a: o.fields.__setitem__("time", a[0]))
        self.m(DT, "before", "(Ljava/util/Date;)Z", lambda o, a: 1 if o.fields.get("time", 0) < a[0].fields.get("time", 0) else 0)
        self.m(DT, "after", "(Ljava/util/Date;)Z", lambda o, a: 1 if o.fields.get("time", 0) > a[0].fields.get("time", 0) else 0)
        self.m(DT, "equals", "(Ljava/lang/Object;)Z",
               lambda o, a: 1 if isinstance(a[0], JavaObject) and a[0].fields.get("time") == o.fields.get("time") else 0)
        self.superclass_map[DT] = "java/lang/Object"

    def _timer_schedule_at(self, timer_obj, task_obj, date_obj, period):
        target_millis = date_obj.fields.get("time", 0) if isinstance(date_obj, JavaObject) else 0
        delay = max(0, target_millis - int(time.time() * 1000))
        self._timer_schedule(timer_obj, task_obj, delay, period)

    def _timertask_cancel(self, o):
        already_cancelled = o.native_companion.get("cancelled", False)
        o.native_companion["cancelled"] = True
        return 0 if already_cancelled else 1

    def _timer_schedule(self, timer_obj, task_obj, delay, period):
        ts = timer_obj.native_companion
        tk = task_obj.native_companion

        def loop():
            time.sleep(max(0, delay) / 1000.0)
            while not ts.cancelled and not tk.get("cancelled"):
                self.host._safe_call(self.engine.invoke_virtual, task_obj, task_obj.class_name, "run", "()V", [])
                if period is None:
                    return
                time.sleep(max(1, period) / 1000.0)

        t = threading.Thread(target=loop, daemon=True)
        ts.workers.append(t)
        t.start()

    # ---- javax/microedition/midlet/MIDlet -------------------------------------
    def _reg_midlet(self):
        C = "javax/microedition/midlet/MIDlet"
        self.m(C, "<init>", "()V", lambda o, a: None)
        self.m(C, "notifyDestroyed", "()V", lambda o, a: self.host.request_exit())
        self.m(C, "notifyPaused", "()V", lambda o, a: None)
        self.m(C, "resumeRequest", "()V", lambda o, a: None)
        self.m(C, "platformRequest", "(Ljava/lang/String;)Z", lambda o, a: 0)
        self.m(C, "getAppProperty", "(Ljava/lang/String;)Ljava/lang/String;", lambda o, a: self._get_app_property(_s(a[0])))
        self.m(C, "checkPermission", "(Ljava/lang/String;)I", lambda o, a: 1)

    def _get_app_property(self, key):
        if not hasattr(self, "_manifest_cache"):
            self._manifest_cache = self._parse_manifest()
        return self._manifest_cache.get(key)

    def _parse_manifest(self):
        props = {}
        try:
            data = self.cl.read_resource("META-INF/MANIFEST.MF")
            if data:
                text = data.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        props[k.strip()] = v.strip()
        except Exception:
            pass
        return props

    # ---- javax/microedition/lcdui/Display --------------------------------------
    def _reg_display(self):
        C = "javax/microedition/lcdui/Display"
        self.m(C, "getDisplay", "(Ljavax/microedition/midlet/MIDlet;)Ljavax/microedition/lcdui/Display;",
               lambda o, a: self._get_display())
        self.m(C, "setCurrent", "(Ljavax/microedition/lcdui/Displayable;)V", lambda o, a: self._display_set_current(a[0]))
        self.m(C, "setCurrent", "(Ljavax/microedition/lcdui/Alert;Ljavax/microedition/lcdui/Displayable;)V",
               lambda o, a: self._display_set_current_alert(a[0], a[1]))
        self.m(C, "setCurrentItem", "(Ljavax/microedition/lcdui/Item;)V", lambda o, a: self._set_current_item(a[0]))
        self.m(C, "getCurrent", "()Ljavax/microedition/lcdui/Displayable;", lambda o, a: self.host.current_displayable)
        self.m(C, "vibrate", "(I)Z", lambda o, a: 1)
        self.m(C, "isColor", "()Z", lambda o, a: 1)
        self.m(C, "numColors", "()I", lambda o, a: 65536)
        self.m(C, "flashBacklight", "(I)Z", lambda o, a: 1)
        self.m(C, "getBestImageWidth", "(I)I", lambda o, a: self.host.width)
        self.m(C, "getBestImageHeight", "(I)I", lambda o, a: self.host.height)

    def _display_set_current(self, displayable):
        if isinstance(displayable, JavaObject) and self.engine.is_instance_of(
                displayable.class_name, "javax/microedition/lcdui/Alert"):
            # setCurrent(Alert) with no explicit "next" screen: real MIDP
            # reverts to whatever was showing before the alert once it's
            # dismissed, so use the current displayable (if any) as next.
            self._display_set_current_alert(displayable, self.host.current_displayable)
            return
        self.host.set_current(displayable)

    def _display_set_current_alert(self, alert_obj, next_displayable):
        if not isinstance(alert_obj, JavaObject):
            return
        alert_obj.fields["_next_displayable"] = next_displayable
        alert_obj.fields["_dismissed"] = False
        st = alert_obj.native_companion
        st.generation += 1
        self.host.set_current(alert_obj)
        timeout = self._alert_get_timeout(alert_obj)
        if timeout != ALERT_FOREVER:
            self._alert_start_timer(alert_obj, timeout, st.generation)

    def _set_current_item(self, item_obj):
        if not isinstance(item_obj, JavaObject):
            return
        form_obj = item_obj.fields.get("_parent_form")
        if not isinstance(form_obj, JavaObject):
            return
        st = form_obj.native_companion
        if item_obj in st.items:
            st.cursor = st.items.index(item_obj)
        self.host.set_current(form_obj)

    def _get_display(self):
        if self._display_singleton is None:
            self._display_singleton = JavaObject("javax/microedition/lcdui/Display", {})
        return self._display_singleton

    # ---- javax/microedition/lcdui/Canvas + GameCanvas ---------------------------
    def _reg_canvas(self):
        for C in ("javax/microedition/lcdui/Canvas", "javax/microedition/lcdui/game/GameCanvas"):
            self.m(C, "getWidth", "()I", lambda o, a: self._canvas_dims(o)[0])
            self.m(C, "getHeight", "()I", lambda o, a: self._canvas_dims(o)[1])
            self.m(C, "repaint", "()V", lambda o, a: self.host.repaint(o))
            self.m(C, "repaint", "(IIII)V", lambda o, a: self.host.repaint(o))
            self.m(C, "serviceRepaints", "()V", lambda o, a: self.host.repaint(o))
            self.m(C, "isDoubleBuffered", "()Z", lambda o, a: 1)
            self.m(C, "hasPointerEvents", "()Z", lambda o, a: 0)
            self.m(C, "hasPointerMotionEvents", "()Z", lambda o, a: 0)
            self.m(C, "hasRepeatEvents", "()Z", lambda o, a: 1)
            self.m(C, "setFullScreenMode", "(Z)V", lambda o, a: self._set_fullscreen(o, bool(a[0])))
            self.m(C, "getGameAction", "(I)I", lambda o, a: K.game_action_for(a[0]))
            self.m(C, "getKeyName", "(I)Ljava/lang/String;", lambda o, a: K.LABELS.get(a[0], str(a[0])))
            self.m(C, "setTitle", "(Ljava/lang/String;)V", lambda o, a: o.fields.__setitem__("title", a[0]))
            self.m(C, "getTitle", "()Ljava/lang/String;", lambda o, a: o.fields.get("title"))
            self.m(C, "keyPressed", "(I)V", lambda o, a: None)
            self.m(C, "keyReleased", "(I)V", lambda o, a: None)
            self.m(C, "keyRepeated", "(I)V", lambda o, a: None)
            self.m(C, "showNotify", "()V", lambda o, a: None)
            self.m(C, "hideNotify", "()V", lambda o, a: None)
            self.m(C, "sizeChanged", "(II)V", lambda o, a: None)
            # Real Canvas.paint() is abstract, so a concrete leaf class always
            # provides its own override, and normal dispatch should never
            # need this. It's here as a safety net: a super.paint(g) call
            # reaching this native fallback (e.g. through a class-hierarchy
            # edge case) is a no-op, not a crash or a silently-swallowed
            # unimplemented-method warning -- the leaf class's own paint()
            # is where the actual rendering happens either way.
            self.m(C, "paint", "(Ljavax/microedition/lcdui/Graphics;)V", lambda o, a: None)
            for name, val in (("UP", K.GAME_UP), ("DOWN", K.GAME_DOWN), ("LEFT", K.GAME_LEFT),
                               ("RIGHT", K.GAME_RIGHT), ("FIRE", K.GAME_FIRE), ("GAME_A", K.GAME_A),
                               ("GAME_B", K.GAME_B), ("GAME_C", K.GAME_C), ("GAME_D", K.GAME_D),
                               ("KEY_NUM0", 48), ("KEY_NUM1", 49), ("KEY_NUM2", 50), ("KEY_NUM3", 51),
                               ("KEY_NUM4", 52), ("KEY_NUM5", 53), ("KEY_NUM6", 54), ("KEY_NUM7", 55),
                               ("KEY_NUM8", 56), ("KEY_NUM9", 57), ("KEY_STAR", K.KEY_STAR), ("KEY_POUND", K.KEY_POUND)):
                self.sf(C, name, val)
        # GameCanvas extras
        GC = "javax/microedition/lcdui/game/GameCanvas"
        self.m(GC, "getGraphics", "()Ljavax/microedition/lcdui/Graphics;", lambda o, a: self.host.get_offscreen_graphics(o))
        self.m(GC, "flushGraphics", "()V", lambda o, a: self.host.repaint(o))
        self.m(GC, "getKeyStates", "()I", lambda o, a: self.host.get_key_states())
        self.sf(GC, "UP_PRESSED", 1 << 1); self.sf(GC, "DOWN_PRESSED", 1 << 6)
        self.sf(GC, "LEFT_PRESSED", 1 << 2); self.sf(GC, "RIGHT_PRESSED", 1 << 5)
        self.sf(GC, "FIRE_PRESSED", 1 << 8)
        self.sf(GC, "GAME_A_PRESSED", 1 << 9); self.sf(GC, "GAME_B_PRESSED", 1 << 10)

    # ---- javax/microedition/lcdui/Graphics ---------------------------------------
    def _reg_graphics(self):
        C = "javax/microedition/lcdui/Graphics"
        for name, val in (("HCENTER", GFX_HCENTER), ("VCENTER", GFX_VCENTER), ("LEFT", GFX_LEFT),
                           ("RIGHT", GFX_RIGHT), ("TOP", GFX_TOP), ("BOTTOM", GFX_BOTTOM),
                           ("BASELINE", GFX_BASELINE), ("SOLID", GFX_SOLID), ("DOTTED", GFX_DOTTED)):
            self.sf(C, name, val)
        self.m(C, "setColor", "(III)V", lambda o, a: setattr(o.native_companion, "color", tuple(a)))
        self.m(C, "setColor", "(I)V", lambda o, a: setattr(o.native_companion, "color", _rgb_from_int(a[0])))
        self.m(C, "getColor", "()I", lambda o, a: _int_from_rgb(o.native_companion.color))
        self.m(C, "getRedComponent", "()I", lambda o, a: o.native_companion.color[0])
        self.m(C, "getGreenComponent", "()I", lambda o, a: o.native_companion.color[1])
        self.m(C, "getBlueComponent", "()I", lambda o, a: o.native_companion.color[2])
        self.m(C, "setGrayScale", "(I)V", lambda o, a: setattr(o.native_companion, "color", (a[0], a[0], a[0])))
        self.m(C, "drawLine", "(IIII)V", lambda o, a: self._g_line(o, a))
        self.m(C, "drawRect", "(IIII)V", lambda o, a: self._g_rect(o, a, fill=False))
        self.m(C, "fillRect", "(IIII)V", lambda o, a: self._g_rect(o, a, fill=True))
        self.m(C, "drawRoundRect", "(IIIIII)V", lambda o, a: self._g_roundrect(o, a, fill=False))
        self.m(C, "fillRoundRect", "(IIIIII)V", lambda o, a: self._g_roundrect(o, a, fill=True))
        self.m(C, "drawArc", "(IIIIII)V", lambda o, a: self._g_arc(o, a, fill=False))
        self.m(C, "fillArc", "(IIIIII)V", lambda o, a: self._g_arc(o, a, fill=True))
        self.m(C, "fillTriangle", "(IIIIII)V", lambda o, a: self._g_triangle(o, a))
        self.m(C, "drawString", "(Ljava/lang/String;III)V", lambda o, a: self._g_string(o, a))
        self.m(C, "drawChar", "(CIII)V", lambda o, a: self._g_string(o, (chr(a[0]), a[1], a[2], a[3])))
        self.m(C, "drawChars", "([CIIIII)V", lambda o, a: self._g_chars(o, a))
        self.m(C, "drawSubstring", "(Ljava/lang/String;IIIII)V",
               lambda o, a: self._g_string(o, (_s(a[0])[a[1]:a[1] + a[2]], a[3], a[4], a[5])))
        self.m(C, "drawImage", "(Ljavax/microedition/lcdui/Image;III)V", lambda o, a: self._g_image(o, a))
        self.m(C, "drawRegion", "(Ljavax/microedition/lcdui/Image;IIIIIIII)V", lambda o, a: self._g_region(o, a))
        self.m(C, "setClip", "(IIII)V", lambda o, a: self._g_setclip(o, a))
        self.m(C, "clipRect", "(IIII)V", lambda o, a: self._g_setclip(o, a))  # simplified: same as setClip
        self.m(C, "getClipX", "()I", lambda o, a: (o.native_companion.surface.get_clip().x))
        self.m(C, "getClipY", "()I", lambda o, a: (o.native_companion.surface.get_clip().y))
        self.m(C, "getClipWidth", "()I", lambda o, a: (o.native_companion.surface.get_clip().w))
        self.m(C, "getClipHeight", "()I", lambda o, a: (o.native_companion.surface.get_clip().h))
        self.m(C, "translate", "(II)V", lambda o, a: self._g_translate(o, a))
        self.m(C, "getTranslateX", "()I", lambda o, a: o.native_companion.tx)
        self.m(C, "getTranslateY", "()I", lambda o, a: o.native_companion.ty)
        self.m(C, "setFont", "(Ljavax/microedition/lcdui/Font;)V", lambda o, a: setattr(o.native_companion, "font_obj", a[0]))
        self.m(C, "getFont", "()Ljavax/microedition/lcdui/Font;", lambda o, a: o.native_companion.font_obj)
        self.m(C, "setStrokeStyle", "(I)V", lambda o, a: None)
        self.m(C, "getStrokeStyle", "()I", lambda o, a: GFX_SOLID)

    def _gxy(self, o, x, y):
        gs = o.native_companion
        return gs.tx + x, gs.ty + y

    def _g_line(self, o, a):
        gs = o.native_companion
        x1, y1 = self._gxy(o, a[0], a[1]); x2, y2 = self._gxy(o, a[2], a[3])
        pygame.draw.line(gs.surface, gs.color, (x1, y1), (x2, y2))

    def _g_rect(self, o, a, fill):
        gs = o.native_companion
        x, y = self._gxy(o, a[0], a[1]); w, h = a[2], a[3]
        rect = pygame.Rect(x, y, w + (1 if not fill else 0), h + (1 if not fill else 0))
        pygame.draw.rect(gs.surface, gs.color, rect, 0 if fill else 1)

    def _g_roundrect(self, o, a, fill):
        gs = o.native_companion
        x, y = self._gxy(o, a[0], a[1]); w, h, aw, ah = a[2], a[3], a[4], a[5]
        rect = pygame.Rect(x, y, w, h)
        radius = max(0, min(aw, ah) // 2)
        pygame.draw.rect(gs.surface, gs.color, rect, 0 if fill else 1, border_radius=radius)

    def _g_arc(self, o, a, fill):
        gs = o.native_companion
        x, y = self._gxy(o, a[0], a[1]); w, h, start, arc = a[2], a[3], a[4], a[5]
        rect = pygame.Rect(x, y, w, h)
        a1 = math.radians(start); a2 = math.radians(start + arc)
        if fill:
            pygame.draw.arc(gs.surface, gs.color, rect, a1, a2, max(w, h))
        else:
            pygame.draw.arc(gs.surface, gs.color, rect, a1, a2, 1)

    def _g_triangle(self, o, a):
        gs = o.native_companion
        pts = [self._gxy(o, a[0], a[1]), self._gxy(o, a[2], a[3]), self._gxy(o, a[4], a[5])]
        pygame.draw.polygon(gs.surface, gs.color, pts)

    def _g_string(self, o, a):
        gs = o.native_companion
        text, x, y, anchor = _s(a[0]) or "", a[1], a[2], (a[3] if len(a) > 3 else (GFX_TOP | GFX_LEFT))
        font = gs.font()
        surf = font.render(text, True, gs.color)
        w, h = surf.get_size()
        dx, dy = x, y
        if anchor & GFX_HCENTER:
            dx -= w // 2
        elif anchor & GFX_RIGHT:
            dx -= w
        if anchor & GFX_VCENTER:
            dy -= h // 2
        elif anchor & GFX_BOTTOM:
            dy -= h
        elif anchor & GFX_BASELINE:
            dy -= font.get_ascent()
        px, py = self._gxy(o, dx, dy)
        gs.surface.blit(surf, (px, py))

    def _g_chars(self, o, a):
        arr, offset, length, x, y, anchor = a
        text = "".join(chr(c) for c in arr.values[offset:offset + length])
        self._g_string(o, (text, x, y, anchor))

    def _g_image(self, o, a):
        img, x, y, anchor = a
        gs = o.native_companion
        surf = self._require(img, "Image", "Graphics.drawImage")
        w, h = surf.get_size()
        dx, dy = x, y
        if anchor & GFX_HCENTER:
            dx -= w // 2
        elif anchor & GFX_RIGHT:
            dx -= w
        if anchor & GFX_VCENTER:
            dy -= h // 2
        elif anchor & GFX_BOTTOM:
            dy -= h
        px, py = self._gxy(o, dx, dy)
        gs.surface.blit(surf, (px, py))

    def _g_region(self, o, a):
        img, xsrc, ysrc, wsrc, hsrc, transform, xdst, ydst, anchor = a
        gs = o.native_companion
        img_surf = self._require(img, "Image", "Graphics.drawRegion")
        src = img_surf.subsurface(pygame.Rect(xsrc, ysrc, wsrc, hsrc))
        frame_img = src
        if transform:
            frame_img = _apply_transform(src, transform)
        w, h = frame_img.get_size()
        dx, dy = xdst, ydst
        if anchor & GFX_HCENTER:
            dx -= w // 2
        elif anchor & GFX_RIGHT:
            dx -= w
        if anchor & GFX_VCENTER:
            dy -= h // 2
        elif anchor & GFX_BOTTOM:
            dy -= h
        px, py = self._gxy(o, dx, dy)
        gs.surface.blit(frame_img, (px, py))

    def _g_setclip(self, o, a):
        gs = o.native_companion
        x, y = self._gxy(o, a[0], a[1])
        gs.surface.set_clip(pygame.Rect(x, y, a[2], a[3]))

    def _g_translate(self, o, a):
        gs = o.native_companion
        gs.tx += a[0]; gs.ty += a[1]

    # ---- javax/microedition/lcdui/Image ------------------------------------------
    def _reg_image(self):
        C = "javax/microedition/lcdui/Image"
        self.m(C, "createImage", "(II)Ljavax/microedition/lcdui/Image;", lambda o, a: self._img_create_blank(a))
        self.m(C, "createImage", "(Ljava/lang/String;)Ljavax/microedition/lcdui/Image;", lambda o, a: self._img_from_resource(a))
        self.m(C, "createImage", "(Ljavax/microedition/lcdui/Image;)Ljavax/microedition/lcdui/Image;", lambda o, a: self._img_copy(a))
        self.m(C, "createImage", "([BII)Ljavax/microedition/lcdui/Image;", lambda o, a: self._img_from_bytes(a))
        self.m(C, "createImage",
               "(Ljavax/microedition/lcdui/Image;IIIII)Ljavax/microedition/lcdui/Image;",
               lambda o, a: self._img_region(a))
        self.m(C, "getWidth", "()I", lambda o, a: o.native_companion.get_width())
        self.m(C, "getHeight", "()I", lambda o, a: o.native_companion.get_height())
        self.m(C, "isMutable", "()Z", lambda o, a: 1 if o.fields.get("mutable") else 0)
        self.m(C, "getGraphics", "()Ljavax/microedition/lcdui/Graphics;", lambda o, a: self._img_graphics(o))

    def _wrap_image(self, surface, mutable=False):
        obj = JavaObject("javax/microedition/lcdui/Image", {"mutable": mutable})
        obj.native_companion = surface
        return obj

    def _img_create_blank(self, a):
        w, h = a
        surf = pygame.Surface((max(1, w), max(1, h)), pygame.SRCALPHA)
        return self._wrap_image(surf, mutable=True)

    def _img_from_resource(self, a):
        path = _s(a[0])
        data = self.cl.read_resource(path)
        if data is None:
            similar = self.cl.find_similar_resources(path)
            hint = f" -- similar entries in jar: {similar}" if similar else " -- no similarly-named entries in jar either"
            self.host.log("warn", f"image resource not found: {path!r}{hint}")
            surf = pygame.Surface((1, 1), pygame.SRCALPHA)
        else:
            surf = pygame.image.load(io.BytesIO(data)).convert_alpha()
        return self._wrap_image(surf, mutable=False)

    def _img_from_bytes(self, a):
        data, offset, length = a
        raw = bytes((v & 0xFF) for v in data.values[offset:offset + length])
        surf = pygame.image.load(io.BytesIO(raw)).convert_alpha()
        return self._wrap_image(surf, mutable=False)

    def _img_copy(self, a):
        return self._wrap_image(self._require(a[0], "Image", "Image.createImage(Image)").copy(), mutable=False)

    def _img_region(self, a):
        src_obj, x, y, w, h, transform = a
        src_surf = self._require(src_obj, "Image", "Image.createImage(Image,x,y,w,h,transform)")
        sub = src_surf.subsurface(pygame.Rect(x, y, w, h)).copy()
        if transform:
            sub = _apply_transform(sub, transform)
        return self._wrap_image(sub, mutable=False)

    def _img_graphics(self, o):
        gfx = JavaObject("javax/microedition/lcdui/Graphics", {})
        gfx.native_companion = GraphicsSurface(o.native_companion, self._font_cache)
        return gfx

    # ---- javax/microedition/lcdui/Font -------------------------------------------
    def _reg_font(self):
        C = "javax/microedition/lcdui/Font"
        self.sf(C, "FACE_SYSTEM", 0); self.sf(C, "FACE_MONOSPACE", 32); self.sf(C, "FACE_PROPORTIONAL", 64)
        self.sf(C, "STYLE_PLAIN", 0); self.sf(C, "STYLE_BOLD", 1); self.sf(C, "STYLE_ITALIC", 2)
        self.sf(C, "SIZE_SMALL", 8); self.sf(C, "SIZE_MEDIUM", 0); self.sf(C, "SIZE_LARGE", 16)
        self.m(C, "getDefaultFont", "()Ljavax/microedition/lcdui/Font;", lambda o, a: self._font_obj(0, 0, 0))
        self.m(C, "getFont", "(III)Ljavax/microedition/lcdui/Font;", lambda o, a: self._font_obj(*a))
        self.m(C, "stringWidth", "(Ljava/lang/String;)I", lambda o, a: self._pygame_font(o).size(_s(a[0]))[0])
        self.m(C, "charWidth", "(C)I", lambda o, a: self._pygame_font(o).size(chr(a[0]))[0])
        self.m(C, "getHeight", "()I", lambda o, a: self._pygame_font(o).get_height())
        self.m(C, "getStyle", "()I", lambda o, a: o.fields.get("style", 0))
        self.m(C, "getSize", "()I", lambda o, a: o.fields.get("size", 0))

    def _font_obj(self, face, style, size):
        obj = JavaObject("javax/microedition/lcdui/Font", {"face": face, "style": style, "size": size})
        return obj

    def _pygame_font(self, font_obj):
        return self._font_cache.get(font_obj)

    # ---- javax/microedition/lcdui/Command, CommandListener, Form (minimal) -----
    def _reg_command_and_form(self):
        C = "javax/microedition/lcdui/Command"
        self.m(C, "<init>", "(Ljava/lang/String;II)V", lambda o, a: o.fields.update({"label": a[0], "type": a[1], "priority": a[2]}))
        self.m(C, "<init>", "(Ljava/lang/String;Ljava/lang/String;II)V",
               lambda o, a: o.fields.update({"label": a[0], "longLabel": a[1], "type": a[2], "priority": a[3]}))
        self.m(C, "getLabel", "()Ljava/lang/String;", lambda o, a: o.fields.get("label"))
        self.m(C, "getLongLabel", "()Ljava/lang/String;", lambda o, a: o.fields.get("longLabel"))
        self.m(C, "getCommandType", "()I", lambda o, a: o.fields.get("type", 0))
        self.m(C, "getPriority", "()I", lambda o, a: o.fields.get("priority", 0))
        for name, val in (("SCREEN", 1), ("BACK", 2), ("CANCEL", 3), ("OK", 4),
                           ("HELP", 5), ("STOP", 6), ("EXIT", 7), ("ITEM", 8)):
            self.sf(C, name, val)

        D = "javax/microedition/lcdui/Displayable"
        self.m(D, "addCommand", "(Ljavax/microedition/lcdui/Command;)V", lambda o, a: self._add_command(o, a[0]))
        self.m(D, "removeCommand", "(Ljavax/microedition/lcdui/Command;)V", lambda o, a: self._remove_command(o, a[0]))
        self.m(D, "setCommandListener", "(Ljavax/microedition/lcdui/CommandListener;)V",
               lambda o, a: self.host.set_command_listener(o, a[0]))
        self.m(D, "setTitle", "(Ljava/lang/String;)V", lambda o, a: o.fields.__setitem__("title", a[0]))
        self.m(D, "getTitle", "()Ljava/lang/String;", lambda o, a: o.fields.get("title"))
        self.m(D, "isShown", "()Z", lambda o, a: 1 if self.host.current_displayable is o else 0)

        F = "javax/microedition/lcdui/Form"
        self.m(F, "<init>", "(Ljava/lang/String;)V", lambda o, a: o.fields.__setitem__("title", a[0]))
        self.m(F, "append", "(Ljava/lang/String;)I", lambda o, a: self._form_append(o, _s(a[0])))
        self.m(F, "append", "(Ljavax/microedition/lcdui/Item;)I", lambda o, a: self._form_append(o, a[0]))
        self.m(F, "append", "(Ljavax/microedition/lcdui/Image;)I", lambda o, a: self._form_append(o, a[0]))
        self.m(F, "get", "(I)Ljavax/microedition/lcdui/Item;", lambda o, a: o.native_companion.items[a[0]])
        self.m(F, "set", "(ILjavax/microedition/lcdui/Item;)V", lambda o, a: self._form_set(o, a[0], a[1]))
        self.m(F, "insert", "(ILjavax/microedition/lcdui/Item;)V", lambda o, a: self._form_insert(o, a[0], a[1]))
        self.m(F, "delete", "(I)V", lambda o, a: self._form_delete(o, a[0]))
        self.m(F, "deleteAll", "()V", lambda o, a: (o.native_companion.items.clear(), setattr(o.native_companion, "cursor", 0)))
        self.m(F, "size", "()I", lambda o, a: len(o.native_companion.items))
        self.m(F, "setItemStateListener", "(Ljavax/microedition/lcdui/ItemStateListener;)V",
               lambda o, a: o.fields.__setitem__("_item_state_listener", a[0]))

        IT = "javax/microedition/lcdui/Item"
        self.m(IT, "getLabel", "()Ljava/lang/String;", lambda o, a: o.fields.get("label"))
        self.m(IT, "setLabel", "(Ljava/lang/String;)V", lambda o, a: o.fields.__setitem__("label", a[0]))
        self.m(IT, "addCommand", "(Ljavax/microedition/lcdui/Command;)V", lambda o, a: self.host.add_command(o, a[0]))
        self.m(IT, "removeCommand", "(Ljavax/microedition/lcdui/Command;)V", lambda o, a: self._remove_command(o, a[0]))
        self.m(IT, "setDefaultCommand", "(Ljavax/microedition/lcdui/Command;)V",
               lambda o, a: o.fields.__setitem__("_default_command", a[0]))
        self.m(IT, "setItemCommandListener", "(Ljavax/microedition/lcdui/ItemCommandListener;)V",
               lambda o, a: o.fields.__setitem__("_item_command_listener", a[0]))
        self.superclass_map[IT] = "java/lang/Object"

        SI = "javax/microedition/lcdui/StringItem"
        self.m(SI, "<init>", "(Ljava/lang/String;Ljava/lang/String;)V",
               lambda o, a: o.fields.update({"label": a[0], "text": a[1]}))
        self.m(SI, "getText", "()Ljava/lang/String;", lambda o, a: o.fields.get("text"))
        self.m(SI, "setText", "(Ljava/lang/String;)V", lambda o, a: o.fields.__setitem__("text", a[0]))
        self.m(SI, "getFont", "()Ljavax/microedition/lcdui/Font;", lambda o, a: o.fields.get("font"))
        self.m(SI, "setFont", "(Ljavax/microedition/lcdui/Font;)V", lambda o, a: o.fields.__setitem__("font", a[0]))
        self.superclass_map[SI] = IT

        II = "javax/microedition/lcdui/ImageItem"
        for name, val in (("LAYOUT_DEFAULT", 0), ("LAYOUT_LEFT", 1), ("LAYOUT_RIGHT", 2), ("LAYOUT_CENTER", 3),
                           ("LAYOUT_NEWLINE_BEFORE", 4), ("LAYOUT_NEWLINE_AFTER", 8),
                           ("PLAIN", 0), ("HYPERLINK", 1), ("BUTTON", 2)):
            self.sf(II, name, val)
        self.m(II, "<init>", "(Ljava/lang/String;Ljavax/microedition/lcdui/Image;IILjava/lang/String;)V",
               lambda o, a: o.fields.update({"label": a[0], "image": a[1], "layout": a[2], "altText": a[4]}))
        self.m(II, "<init>", "(Ljava/lang/String;Ljavax/microedition/lcdui/Image;IILjava/lang/String;I)V",
               lambda o, a: o.fields.update({"label": a[0], "image": a[1], "layout": a[2], "altText": a[4], "appearanceMode": a[5]}))
        self.m(II, "getImage", "()Ljavax/microedition/lcdui/Image;", lambda o, a: o.fields.get("image"))
        self.m(II, "setImage", "(Ljavax/microedition/lcdui/Image;)V", lambda o, a: o.fields.__setitem__("image", a[0]))
        self.m(II, "getAltText", "()Ljava/lang/String;", lambda o, a: o.fields.get("altText"))
        self.m(II, "getAppearanceMode", "()I", lambda o, a: o.fields.get("appearanceMode", 0))
        self.superclass_map[II] = IT

        G = "javax/microedition/lcdui/Gauge"
        self.sf(G, "INDEFINITE", -1)
        self.m(G, "<init>", "(Ljava/lang/String;ZII)V",
               lambda o, a: o.fields.update({"label": a[0], "interactive": a[1], "max": a[2], "value": a[3]}))
        self.m(G, "getValue", "()I", lambda o, a: o.fields.get("value", 0))
        self.m(G, "setValue", "(I)V", lambda o, a: o.fields.__setitem__("value", max(0, min(o.fields.get("max", 0), a[0]))))
        self.m(G, "getMaxValue", "()I", lambda o, a: o.fields.get("max", 0))
        self.m(G, "setMaxValue", "(I)V", lambda o, a: o.fields.__setitem__("max", a[0]))
        self.m(G, "isInteractive", "()Z", lambda o, a: 1 if o.fields.get("interactive") else 0)
        self.superclass_map[G] = IT
        # NOTE: only interactive when embedded in a Form does a Gauge
        # respond to input (LEFT/RIGHT nudge its value while focused --
        # see NativeBridge.form_adjust); there's no touch/drag simulation.

        TF = "javax/microedition/lcdui/TextField"
        self.sf(TF, "ANY", 0); self.sf(TF, "EMAILADDR", 1); self.sf(TF, "NUMERIC", 2)
        self.sf(TF, "PHONENUMBER", 3); self.sf(TF, "URL", 4); self.sf(TF, "DECIMAL", 5)
        self.sf(TF, "PASSWORD", 0x10000); self.sf(TF, "UNEDITABLE", 0x20000); self.sf(TF, "SENSITIVE", 0x40000)
        self.sf(TF, "NON_PREDICTIVE", 0x80000); self.sf(TF, "INITIAL_CAPS_WORD", 0x100000)
        self.sf(TF, "INITIAL_CAPS_SENTENCE", 0x200000)
        self.m(TF, "<init>", "(Ljava/lang/String;Ljava/lang/String;II)V",
               lambda o, a: o.fields.update({"label": a[0], "text": a[1] or "", "maxSize": a[2], "constraints": a[3]}))
        self.m(TF, "getString", "()Ljava/lang/String;", lambda o, a: o.fields.get("text", ""))
        self.m(TF, "setString", "(Ljava/lang/String;)V", lambda o, a: o.fields.__setitem__("text", a[0] or ""))
        self.m(TF, "getMaxSize", "()I", lambda o, a: o.fields.get("maxSize", 0))
        self.m(TF, "size", "()I", lambda o, a: len(o.fields.get("text", "")))
        self.m(TF, "getConstraints", "()I", lambda o, a: o.fields.get("constraints", 0))
        self.m(TF, "setConstraints", "(I)V", lambda o, a: o.fields.__setitem__("constraints", a[0]))
        self.superclass_map[TF] = IT


        SP = "javax/microedition/lcdui/Spacer"
        self.m(SP, "<init>", "(II)V", lambda o, a: o.fields.update({"_min_width": a[0], "_min_height": a[1]}))
        self.m(SP, "setMinimumSize", "(II)V", lambda o, a: o.fields.update({"_min_width": a[0], "_min_height": a[1]}))

        self.m(SP, "addCommand", "(Ljavax/microedition/lcdui/Command;)V",
               lambda o, a: self.host.engine_throw("java/lang/IllegalStateException", "Spacer cannot have Commands"))
        self.m(SP, "setDefaultCommand", "(Ljavax/microedition/lcdui/Command;)V",
               lambda o, a: self.host.engine_throw("java/lang/IllegalStateException", "Spacer cannot have Commands"))
        self.superclass_map[SP] = IT

        DF = "javax/microedition/lcdui/DateField"
        self.sf(DF, "DATE", 1); self.sf(DF, "TIME", 2); self.sf(DF, "DATE_TIME", 3)
        self.m(DF, "<init>", "(Ljava/lang/String;I)V", lambda o, a: o.fields.update({"label": a[0], "_mode": a[1]}))
        self.m(DF, "getDate", "()Ljava/util/Date;", lambda o, a: o.fields.get("_date"))
        self.m(DF, "setDate", "(Ljava/util/Date;)V", lambda o, a: o.fields.__setitem__("_date", a[0]))
        self.m(DF, "getInputMode", "()I", lambda o, a: o.fields.get("_mode", 3))
        self.m(DF, "setInputMode", "(I)V", lambda o, a: o.fields.__setitem__("_mode", a[0]))
        self.superclass_map[DF] = IT
        # NOTE: the TimeZone-taking constructor isn't implemented -- this
        # emulator has no java.util.TimeZone support, and MIDlets that only
        # need "some date/time value on a Form" (the overwhelming majority)
        # use the plain 2-arg constructor.

        CI = "javax/microedition/lcdui/CustomItem"
        self.m(CI, "<init>", "(Ljava/lang/String;)V", lambda o, a: o.fields.__setitem__("label", a[0]))
        self.m(CI, "getGameAction", "(I)I", lambda o, a: K.game_action_for(a[0]))
        self.m(CI, "repaint", "()V", lambda o, a: None)
        self.m(CI, "repaint", "(IIII)V", lambda o, a: None)
        self.m(CI, "invalidate", "()V", lambda o, a: None)
        self.superclass_map[CI] = IT
        # NOTE: CustomItem's own paint(Graphics,int,int) is called back into
        # from NativeBridge._render_custom_item whenever a Form containing
        # one is drawn -- see render_form. Pointer/traversal callbacks
        # (pointerPressed, traverse, ...) aren't wired up; paint-only
        # CustomItems (by far the common case: custom-drawn widgets/HUD-like
        # rows inside a Form) work correctly.

        CH = "javax/microedition/lcdui/Choice"
        self.sf(CH, "EXCLUSIVE", CHOICE_EXCLUSIVE); self.sf(CH, "IMPLICIT", CHOICE_IMPLICIT)
        self.sf(CH, "MULTIPLE", CHOICE_MULTIPLE); self.sf(CH, "POPUP", CHOICE_POPUP)

        L = "javax/microedition/lcdui/List"
        CG = "javax/microedition/lcdui/ChoiceGroup"
        self.superclass_map[CG] = IT
        for CLS in (L, CG):
            self.sf(CLS, "EXCLUSIVE", CHOICE_EXCLUSIVE); self.sf(CLS, "IMPLICIT", CHOICE_IMPLICIT)
            self.sf(CLS, "MULTIPLE", CHOICE_MULTIPLE); self.sf(CLS, "POPUP", CHOICE_POPUP)
            self.m(CLS, "append", "(Ljava/lang/String;Ljavax/microedition/lcdui/Image;)I", lambda o, a: self._list_append(o, a))
            self.m(CLS, "insert", "(ILjava/lang/String;Ljavax/microedition/lcdui/Image;)V", lambda o, a: self._list_insert(o, a))
            self.m(CLS, "delete", "(I)V", lambda o, a: self._list_delete(o, a[0]))
            self.m(CLS, "deleteAll", "()V", lambda o, a: (o.native_companion.items.clear(),
                                                            setattr(o.native_companion, "cursor", 0),
                                                            o.native_companion.selected.clear()))
            self.m(CLS, "set", "(ILjava/lang/String;Ljavax/microedition/lcdui/Image;)V",
                   lambda o, a: o.native_companion.items.__setitem__(a[0], (_s(a[1]), a[2])))
            self.m(CLS, "getString", "(I)Ljava/lang/String;", lambda o, a: o.native_companion.items[a[0]][0])
            self.m(CLS, "getImage", "(I)Ljavax/microedition/lcdui/Image;", lambda o, a: o.native_companion.items[a[0]][1])
            self.m(CLS, "size", "()I", lambda o, a: len(o.native_companion.items))
            self.m(CLS, "setSelectedIndex", "(IZ)V", lambda o, a: self._list_set_selected(o, a))
            self.m(CLS, "getSelectedIndex", "()I", lambda o, a: self._list_get_selected(o))
            self.m(CLS, "isSelected", "(I)Z", lambda o, a: self._list_is_selected(o, a[0]))
            self.m(CLS, "getSelectedFlags", "([Z)I", lambda o, a: self._list_get_selected_flags(o, a[0]))
            self.m(CLS, "setSelectedFlags", "([Z)V", lambda o, a: self._list_set_selected_flags(o, a[0]))
            self.m(CLS, "getFont", "(I)Ljavax/microedition/lcdui/Font;", lambda o, a: None)
            self.m(CLS, "setFont", "(ILjavax/microedition/lcdui/Font;)V", lambda o, a: None)

        self.sf(L, "SELECT_COMMAND", lambda: self._default_select_command())
        self.m(L, "<init>", "(Ljava/lang/String;I)V", lambda o, a: self._list_init(o, a[0], a[1], [], []))
        self.m(L, "<init>", "(Ljava/lang/String;I[Ljava/lang/String;[Ljavax/microedition/lcdui/Image;)V",
               lambda o, a: self._list_init(o, a[0], a[1], a[2].values if a[2] else [], a[3].values if a[3] else []))
        self.m(L, "setSelectCommand", "(Ljavax/microedition/lcdui/Command;)V", lambda o, a: setattr(o.native_companion, "select_command", a[0]))
        self.m(L, "setFitPolicy", "(I)V", lambda o, a: None)

        self.m(CG, "<init>", "(Ljava/lang/String;I)V", lambda o, a: self._list_init(o, a[0], a[1], [], []))
        self.m(CG, "<init>", "(Ljava/lang/String;I[Ljava/lang/String;[Ljavax/microedition/lcdui/Image;)V",
               lambda o, a: self._list_init(o, a[0], a[1], a[2].values if a[2] else [], a[3].values if a[3] else []))

    def _list_init(self, o, title, list_type, strings, images):
        # List (a Displayable) exposes this via getTitle() -> "title"; but
        # ChoiceGroup (an Item, sharing this same init since both are
        # Choice) exposes it via getLabel() -> "label". Set both so
        # whichever one the real class actually uses works correctly.
        o.fields["title"] = title
        o.fields["label"] = title
        st = o.native_companion
        st.list_type = list_type
        st.items = [(_s(s), (images[i] if i < len(images) else None)) for i, s in enumerate(strings)]

    def _list_append(self, o, a):
        o.native_companion.items.append((_s(a[0]), a[1]))
        return len(o.native_companion.items) - 1

    def _list_insert(self, o, a):
        idx, label, image = a
        o.native_companion.items.insert(idx, (_s(label), image))

    def _list_delete(self, o, idx):
        st = o.native_companion
        if idx < 0 or idx >= len(st.items):
            self.host.engine_throw("java/lang/IndexOutOfBoundsException", str(idx))
        del st.items[idx]
        st.cursor = max(0, min(st.cursor, len(st.items) - 1))
        st.selected = {i if i < idx else i - 1 for i in st.selected if i != idx}

    def _list_set_selected(self, o, a):
        idx, flag = a
        st = o.native_companion
        if st.list_type == CHOICE_MULTIPLE:  # independent checkbox-style selection
            if flag:
                st.selected.add(idx)
            else:
                st.selected.discard(idx)
        elif flag:
            # IMPLICIT/EXCLUSIVE: selection and the highlighted cursor are
            # the same thing -- there's no "unselected but highlighted"
            # state, so setting selected(idx, true) just moves the cursor.
            st.cursor = idx

    def _list_get_selected(self, o):
        st = o.native_companion
        if st.list_type == CHOICE_MULTIPLE or not st.items:
            return -1
        return st.cursor

    def _list_is_selected(self, o, i):
        st = o.native_companion
        if st.list_type == CHOICE_MULTIPLE:
            return 1 if i in st.selected else 0
        return 1 if i == st.cursor else 0

    def _list_get_selected_flags(self, o, flags_arr):
        st = o.native_companion
        n = min(len(st.items), len(flags_arr.values))
        count = 0
        for i in range(n):
            sel = bool(self._list_is_selected(o, i))
            flags_arr.values[i] = 1 if sel else 0
            count += 1 if sel else 0
        return count

    def _list_set_selected_flags(self, o, flags_arr):
        st = o.native_companion
        if st.list_type == CHOICE_MULTIPLE:
            st.selected = {i for i, v in enumerate(flags_arr.values) if v}
        else:
            for i, v in enumerate(flags_arr.values):
                if v:
                    st.cursor = i
                    break

    def _choice_toggle_at_cursor(self, st):
        if not st.items:
            return
        if st.list_type == CHOICE_MULTIPLE:
            if st.cursor in st.selected:
                st.selected.discard(st.cursor)
            else:
                st.selected.add(st.cursor)


    def _remove_command(self, o, cmd):
        cmds = o.fields.get("_commands")
        if cmds and cmd in cmds:
            cmds.remove(cmd)

    def _form_delete(self, o, i):
        items = o.native_companion.items
        if i < 0 or i >= len(items):
            self.host.engine_throw("java/lang/IndexOutOfBoundsException", str(i))
        items.pop(i)
        st = o.native_companion
        st.cursor = max(0, min(st.cursor, len(items) - 1))

    def _form_append(self, o, item):
        self._form_tag_parent(o, item)
        o.native_companion.items.append(item)
        return len(o.native_companion.items) - 1

    def _form_insert(self, o, idx, item):
        self._form_tag_parent(o, item)
        o.native_companion.items.insert(idx, item)

    def _form_set(self, o, idx, item):
        self._form_tag_parent(o, item)
        o.native_companion.items[idx] = item

    def _form_tag_parent(self, form_obj, item):

        if isinstance(item, JavaObject):
            item.fields["_parent_form"] = form_obj

    def _datefield_text(self, item):
        d = item.fields.get("_date")
        if not isinstance(d, JavaObject):
            return "(not set)"
        millis = d.fields.get("time", 0)
        try:
            dt = datetime.datetime.utcfromtimestamp(millis / 1000.0)
        except (OverflowError, OSError, ValueError):
            return "(invalid)"
        mode = item.fields.get("_mode", 3)
        if mode == 1:  # DATE
            return dt.strftime("%Y-%m-%d")
        if mode == 2:  # TIME
            return dt.strftime("%H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _canvas_dims(self, canvas_obj):
        w = self.host.width
        if not isinstance(canvas_obj, JavaObject) or canvas_obj.fields.get("_fullscreen"):
            return w, self.host.height
        left, right = self._soft_key_commands(canvas_obj)
        if left is None and right is None:
            return w, self.host.height
        bar_h = self._font_cache.get(None).get_height() + 6
        return w, self.host.height - bar_h

    def _maybe_notify_size_changed(self, canvas_obj):
        if not isinstance(canvas_obj, JavaObject):
            return
        if not self.engine.is_instance_of(canvas_obj.class_name, "javax/microedition/lcdui/Canvas"):
            return
        w, h = self._canvas_dims(canvas_obj)
        if canvas_obj.fields.get("_last_w") == w and canvas_obj.fields.get("_last_h") == h:
            return
        canvas_obj.fields["_last_w"] = w
        canvas_obj.fields["_last_h"] = h
        self.host._safe_call(self.engine.invoke_virtual, canvas_obj, canvas_obj.class_name,
                              "sizeChanged", "(II)V", [w, h])

    def _set_fullscreen(self, canvas_obj, flag):
        canvas_obj.fields["_fullscreen"] = flag
        self._maybe_notify_size_changed(canvas_obj)
        if self.host.current_displayable is canvas_obj:
            self.host.repaint(canvas_obj)  # hotbar visibility just changed -- redraw now, don't wait for next event

    def _add_command(self, displayable, command_obj):
        self.host.add_command(displayable, command_obj)
        self._maybe_notify_size_changed(displayable)  # the hotbar may have just appeared

    def _commands_for_hotbar(self, displayable):
        if isinstance(displayable, JavaObject) and self.engine.is_instance_of(
                displayable.class_name, "javax/microedition/lcdui/Alert"):
            cmds = self.host.get_commands(displayable)
            return cmds if cmds else [self._dismiss_command()]
        return self.host.get_commands(displayable)

    def _soft_key_commands(self, displayable):
        commands = self._commands_for_hotbar(displayable)
        if not commands:
            return None, None
        back_cmds = [c for c in commands if c.fields.get("type") in _BACK_COMMAND_TYPES]
        other_cmds = [c for c in commands if c.fields.get("type") not in _BACK_COMMAND_TYPES]
        left_cmd = min(other_cmds, key=lambda c: c.fields.get("priority", 0)) if other_cmds else None
        if back_cmds:
            right_cmd = back_cmds[0]
        else:
            remaining = [c for c in other_cmds if c is not left_cmd]
            right_cmd = min(remaining, key=lambda c: c.fields.get("priority", 0)) if remaining else None
        return left_cmd, right_cmd

    def fire_soft_key_command(self, displayable, midp_keycode):

        if displayable.fields.get("_fullscreen"):
            return False  # fullscreen mode: the app owns all input itself
        listener = self.host.get_command_listener(displayable)
        if listener is None:
            return False
        left_cmd, right_cmd = self._soft_key_commands(displayable)
        chosen = right_cmd if midp_keycode == K.KEY_SOFT_RIGHT else left_cmd
        if chosen is None:
            return False
        self.host._safe_call(self.engine.invoke_virtual, listener, listener.class_name, "commandAction",
                              "(Ljavax/microedition/lcdui/Command;Ljavax/microedition/lcdui/Displayable;)V",
                              [chosen, displayable])
        return True

    def render_hotbar(self, displayable, surface):

        if not isinstance(displayable, JavaObject) or displayable.fields.get("_fullscreen"):
            return
        left_cmd, right_cmd = self._soft_key_commands(displayable)
        if left_cmd is None and right_cmd is None:
            return
        font = self._font_cache.get(None)
        w, h = surface.get_size()
        bar_h = font.get_height() + 6
        pygame.draw.rect(surface, (225, 225, 225), pygame.Rect(0, h - bar_h, w, bar_h))
        pygame.draw.line(surface, (110, 110, 110), (0, h - bar_h), (w, h - bar_h))
        if left_cmd is not None:
            label = left_cmd.fields.get("label", "")
            surface.blit(font.render(label, True, (0, 0, 0)), (4, h - bar_h + 3))
        if right_cmd is not None:
            label = right_cmd.fields.get("label", "")
            rendered = font.render(label, True, (0, 0, 0))
            surface.blit(rendered, (w - rendered.get_width() - 4, h - bar_h + 3))

    # ---- Ticker (shared top-strip renderer for every Displayable type) ---------
    def _render_ticker(self, displayable_obj, surface, y, w):

        ticker = displayable_obj.fields.get("_ticker") if isinstance(displayable_obj, JavaObject) else None
        if not isinstance(ticker, JavaObject):
            return y
        text = ticker.fields.get("_text") or ""
        if not text:
            return y
        font = self._font_cache.get(None)
        bar_h = font.get_height() + 4
        pygame.draw.rect(surface, (25, 25, 25), pygame.Rect(0, y, w, bar_h))
        rendered = font.render(text + "     ", True, (255, 255, 255))
        tw = rendered.get_width()
        offset = int(time.time() * 40) % tw if tw else 0
        surface.set_clip(pygame.Rect(0, y, w, bar_h))
        surface.blit(rendered, (-offset, y + 2))
        surface.blit(rendered, (tw - offset, y + 2))
        surface.set_clip(None)
        return y + bar_h + 2

    def render_ticker_overlay(self, canvas_obj, surface):

        if not isinstance(canvas_obj, JavaObject):
            return
        w, _h = surface.get_size()
        self._render_ticker(canvas_obj, surface, 0, w)

    def displayable_needs_animation(self, displayable):

        if not isinstance(displayable, JavaObject):
            return False
        if isinstance(displayable.fields.get("_ticker"), JavaObject):
            return True
        return self.engine.is_instance_of(displayable.class_name, "javax/microedition/lcdui/TextBox")

    def _draw_wrapped_text(self, surface, font, text, x, y, max_w, color=(0, 0, 0)):

        for paragraph in (text or "").split("\n"):
            line = ""
            for word in paragraph.split(" "):
                candidate = f"{line} {word}".strip() if line else word
                if line and font.size(candidate)[0] > max_w:
                    surface.blit(font.render(line, True, color), (x, y))
                    y += font.get_height() + 2
                    line = word
                else:
                    line = candidate
            surface.blit(font.render(line, True, color), (x, y))
            y += font.get_height() + 2
        return y

    def _hotbar_height(self, displayable, font):
        if isinstance(displayable, JavaObject) and displayable.fields.get("_fullscreen"):
            return 0
        left_cmd, right_cmd = self._soft_key_commands(displayable)
        if left_cmd is None and right_cmd is None:
            return 0
        return font.get_height() + 6

    def render_form(self, form_obj, surface):
        st = form_obj.native_companion
        font = self._font_cache.get(None)
        w, h = surface.get_size()
        surface.set_clip(None)

        tall_h = max(h, 60 + sum(self._form_item_height_budget(it) for it in st.items))
        scratch = pygame.Surface((w, tall_h))
        scratch.fill((255, 255, 255))
        y = 2
        y = self._render_ticker(form_obj, scratch, y, w)
        title = form_obj.fields.get("title")
        if title:
            scratch.blit(font.render(title, True, (0, 0, 0)), (4, y))
            y += font.get_height() + 4
            pygame.draw.line(scratch, (0, 0, 0), (0, y), (w, y))
            y += 2
        content_top = y
        focus_span = (content_top, content_top)
        for i, item in enumerate(st.items):
            item_top = y
            y = self._render_form_item(scratch, font, item, y, w, focused=(i == st.cursor))
            if i == st.cursor:
                focus_span = (item_top, y)

        hotbar_h = self._hotbar_height(form_obj, font)
        effective_bottom = max(content_top + 1, h - hotbar_h)
        scroll = st.scroll_y
        top, bottom = focus_span
        if top - scroll < content_top:
            scroll = top - content_top
        elif bottom - scroll > effective_bottom:
            scroll = bottom - (effective_bottom - content_top)
        st.scroll_y = max(0, min(scroll, max(0, y - effective_bottom)))

        surface.fill((255, 255, 255))
        surface.blit(scratch, (0, 0), pygame.Rect(0, 0, w, content_top))
        surface.blit(scratch, (0, content_top), pygame.Rect(0, content_top + st.scroll_y, w, h - content_top))

    def _form_item_height_budget(self, item):
        if not isinstance(item, JavaObject):
            return 40
        if item.class_name == "javax/microedition/lcdui/ChoiceGroup":
            return 40 + 20 * max(1, len(item.native_companion.items))
        if self.engine.is_instance_of(item.class_name, "javax/microedition/lcdui/CustomItem"):
            return 440  # matches _render_custom_item's own 400px content-height cap, plus margin
        return 60

    def _render_form_item(self, surface, font, item, y, w, focused=False):
        row_h = font.get_height() + 3
        marker = "> " if focused else "  "
        if isinstance(item, str):
            surface.blit(font.render(marker + item, True, (0, 0, 0)), (4, y))
            return y + row_h
        if not isinstance(item, JavaObject):
            return y + row_h
        cname = item.class_name
        label = item.fields.get("label")
        if cname == "javax/microedition/lcdui/StringItem":
            text = item.fields.get("text", "")
            line = f"{label}: {text}" if label else text
            surface.blit(font.render(marker + line, True, (0, 0, 0)), (4, y))
            return y + row_h
        if cname == "javax/microedition/lcdui/ImageItem":
            if label:
                surface.blit(font.render(marker + label, True, (0, 0, 0)), (4, y))
                y += row_h
            img = item.fields.get("image")
            if isinstance(img, JavaObject) and img.native_companion is not None:
                surface.blit(img.native_companion, (4, y))
                return y + img.native_companion.get_height() + 4
            return y + row_h
        if cname == "javax/microedition/lcdui/Gauge":
            if label:
                surface.blit(font.render(marker + label, True, (0, 0, 0)), (4, y))
                y += row_h
            maxv = max(1, item.fields.get("max", 1))
            val = item.fields.get("value", 0)
            bar_w = max(0, w - 8)
            pygame.draw.rect(surface, (150, 150, 150), pygame.Rect(4, y, bar_w, 10), 1)
            fill_w = int(bar_w * min(1.0, val / maxv))
            bar_color = (0, 170, 80) if (focused and item.fields.get("interactive")) else (0, 120, 220)
            pygame.draw.rect(surface, bar_color, pygame.Rect(4, y, fill_w, 10))
            return y + 14
        if cname == "javax/microedition/lcdui/TextField":
            text = item.fields.get("text", "")
            masked = bool(item.fields.get("constraints", 0) & 0x10000)
            shown = "*" * len(text) if masked else text
            line = f"{label}: [{shown}]" if label else f"[{shown}]"
            surface.blit(font.render(marker + line, True, (0, 0, 0)), (4, y))
            return y + row_h
        if cname == "javax/microedition/lcdui/ChoiceGroup":
            return self._render_choicegroup_in_form(surface, font, item, y, w, focused)
        if cname == "javax/microedition/lcdui/Spacer":
            return y + max(2, item.fields.get("_min_height", 4))
        if cname == "javax/microedition/lcdui/DateField":
            text = self._datefield_text(item)
            line = f"{label}: {text}" if label else text
            surface.blit(font.render(marker + line, True, (0, 0, 0)), (4, y))
            return y + row_h
        if cname == "javax/microedition/lcdui/CustomItem" or self.engine.is_instance_of(
                cname, "javax/microedition/lcdui/CustomItem"):
            # CustomItem is abstract -- real instances are always a user
            # subclass (e.g. an anonymous inner class), never this literal
            # name, so this needs an instanceof check rather than the exact-
            # match used above for the concrete, not-meant-to-be-subclassed
            # Item types.
            return self._render_custom_item(surface, font, item, y, w)
        # unknown/custom Item subclass -- show its label if it has one so the
        # screen isn't silently missing content
        if label:
            surface.blit(font.render(marker + label, True, (0, 0, 0)), (4, y))
        return y + row_h

    def _render_choicegroup_in_form(self, surface, font, cg_obj, y, w, focused):
        st = cg_obj.native_companion
        label = cg_obj.fields.get("label")
        row_h = font.get_height() + 3
        if label:
            surface.blit(font.render(label, True, (0, 0, 0)), (4, y))
            y += row_h
        for i, (text, _image) in enumerate(st.items):
            hi = focused and i == st.cursor
            if st.list_type == CHOICE_MULTIPLE:
                glyph = "[x] " if i in st.selected else "[ ] "
            else:
                glyph = "(o) " if i == st.cursor else "( ) "
            prefix = "> " if hi else "  "
            color = (0, 90, 200) if hi else (0, 0, 0)
            surface.blit(font.render(prefix + glyph + text, True, color), (12, y))
            y += row_h
        return y

    def _render_custom_item(self, surface, font, item, y, w):
        pref_w = self._safe_content_int(item, "getPrefContentWidth", "(I)I", [-1], default=w - 8)
        pref_h = self._safe_content_int(item, "getPrefContentHeight", "(I)I", [-1], default=font.get_height() + 10)
        pref_w = max(1, min(pref_w, max(1, w - 8)))
        pref_h = max(1, min(pref_h, 400))
        sub = pygame.Surface((pref_w, pref_h))
        sub.fill((255, 255, 255))
        gfx = JavaObject("javax/microedition/lcdui/Graphics", {})
        gfx.native_companion = GraphicsSurface(sub, self._font_cache)
        self.host._safe_call(self.engine.invoke_virtual, item, item.class_name, "paint",
                              "(Ljavax/microedition/lcdui/Graphics;II)V", [gfx, pref_w, pref_h])
        surface.blit(sub, (4, y))
        return y + pref_h + 4

    def _safe_content_int(self, obj, name, desc, args, default):
        result = self.host._safe_call(self.engine.invoke_virtual, obj, obj.class_name, name, desc, args)
        return result if isinstance(result, int) else default

    def render_list(self, list_obj, surface):
        st = list_obj.native_companion
        font = self._font_cache.get(None)
        w, h = surface.get_size()
        surface.set_clip(None)
        surface.fill((255, 255, 255))
        y = 2
        y = self._render_ticker(list_obj, surface, y, w)
        title = list_obj.fields.get("title")
        if title:
            surface.blit(font.render(title, True, (0, 0, 0)), (4, y))
            y += font.get_height() + 4
            pygame.draw.line(surface, (0, 0, 0), (0, y), (w, y))
            y += 2
        row_h = font.get_height() + 4
        hotbar_h = self._hotbar_height(list_obj, font)
        visible_rows = max(1, (h - hotbar_h - y) // row_h)
        if st.cursor < st.scroll_row:
            st.scroll_row = st.cursor
        elif st.cursor >= st.scroll_row + visible_rows:
            st.scroll_row = st.cursor - visible_rows + 1
        st.scroll_row = max(0, min(st.scroll_row, max(0, len(st.items) - visible_rows)))
        for i in range(st.scroll_row, len(st.items)):
            if y + row_h > h:
                break
            label, _image = st.items[i]
            if i == st.cursor:
                pygame.draw.rect(surface, (0, 90, 200), pygame.Rect(0, y, w, row_h))
                color = (255, 255, 255)
            else:
                color = (0, 0, 0)
            mark = "> " if st.list_type != CHOICE_IMPLICIT and self._list_is_selected(list_obj, i) else "  "
            surface.blit(font.render(mark + label, True, color), (4, y + 2))
            y += row_h

    def list_navigate(self, list_obj, direction):
        st = list_obj.native_companion
        if st.items:
            st.cursor = (st.cursor + direction) % len(st.items)

    def list_fire(self, list_obj):
        st = list_obj.native_companion
        if not st.items:
            return
        self._choice_toggle_at_cursor(st)
        listener = self.host.get_command_listener(list_obj)
        if listener is not None:
            cmd = st.select_command or self._default_select_command()
            self.host._safe_call(self.engine.invoke_virtual, listener, listener.class_name, "commandAction",
                                  "(Ljavax/microedition/lcdui/Command;Ljavax/microedition/lcdui/Displayable;)V",
                                  [cmd, list_obj])

    def _default_select_command(self):
        if not hasattr(self, "_select_command_singleton"):
            self._select_command_singleton = JavaObject("javax/microedition/lcdui/Command", {"label": "Select", "type": 4, "priority": 0})
        return self._select_command_singleton

    # ---- Form focus navigation (UP/DOWN/LEFT/RIGHT/FIRE -- see engine.py) ------
    def _focused_form_item(self, form_obj):
        st = form_obj.native_companion
        if not st.items or not (0 <= st.cursor < len(st.items)):
            return None
        return st.items[st.cursor]

    def form_navigate(self, form_obj, direction):
        st = form_obj.native_companion
        if st.items:
            st.cursor = (st.cursor + direction) % len(st.items)

    def form_adjust(self, form_obj, delta):
        item = self._focused_form_item(form_obj)
        if not isinstance(item, JavaObject):
            return
        if item.class_name == "javax/microedition/lcdui/Gauge" and item.fields.get("interactive"):
            maxv = item.fields.get("max", 0)
            val = item.fields.get("value", 0)
            new_val = max(0, min(maxv, val + delta))
            if new_val != val:
                item.fields["value"] = new_val
                self._fire_item_state_changed(form_obj, item)
        elif item.class_name == "javax/microedition/lcdui/ChoiceGroup":
            st = item.native_companion
            if st.items:
                st.cursor = (st.cursor + delta) % len(st.items)

    def form_fire(self, form_obj):
        item = self._focused_form_item(form_obj)
        if not isinstance(item, JavaObject):
            return
        if item.class_name == "javax/microedition/lcdui/ChoiceGroup":
            st = item.native_companion
            if st.items:
                self._choice_toggle_at_cursor(st)
                self._fire_item_state_changed(form_obj, item)
            return
        # The nearest real-MIDP equivalent to FIRE on a plain focused row:
        # an app-registered per-item context command, if any (real devices
        # surface these through a dedicated "Options" menu instead).
        cmd = item.fields.get("_default_command")
        listener = item.fields.get("_item_command_listener")
        if cmd is not None and listener is not None:
            self.host._safe_call(self.engine.invoke_virtual, listener, listener.class_name, "commandAction",
                                  "(Ljavax/microedition/lcdui/Command;Ljavax/microedition/lcdui/Item;)V",
                                  [cmd, item])

    def _fire_item_state_changed(self, form_obj, item):
        listener = form_obj.fields.get("_item_state_listener")
        if listener is not None:
            self.host._safe_call(self.engine.invoke_virtual, listener, listener.class_name, "itemStateChanged",
                                  "(Ljavax/microedition/lcdui/Item;)V", [item])


    # ---- javax/microedition/lcdui/AlertType -------------------------------------
    def _reg_alert_type(self):
        AT = "javax/microedition/lcdui/AlertType"
        self._alert_types = {}
        for name in ("INFO", "WARNING", "ERROR", "ALARM", "CONFIRMATION"):
            obj = JavaObject(AT, {"_name": name})
            self._alert_types[name] = obj
            self.sf(AT, name, obj)
        self.m(AT, "playSound", "(Ljavax/microedition/lcdui/Display;)Z", lambda o, a: 1)
        self.m(AT, "toString", "()Ljava/lang/String;", lambda o, a: o.fields.get("_name", "AlertType"))
        self.superclass_map[AT] = "java/lang/Object"

    _ALERT_BAR_COLORS = {
        "WARNING": (230, 150, 20), "ERROR": (200, 40, 40), "ALARM": (150, 60, 200),
        "CONFIRMATION": (40, 150, 70), "INFO": (30, 110, 210),
    }

    def _alert_bar_color(self, alert_type_obj):
        name = alert_type_obj.fields.get("_name") if isinstance(alert_type_obj, JavaObject) else None
        return self._ALERT_BAR_COLORS.get(name, (120, 120, 120))

    # ---- javax/microedition/lcdui/Alert ------------------------------------------
    def _reg_alert(self):
        AL = "javax/microedition/lcdui/Alert"
        self.sf(AL, "FOREVER", ALERT_FOREVER)
        self.sf(AL, "DISMISS_COMMAND", lambda: self._dismiss_command())
        self.m(AL, "<init>", "(Ljava/lang/String;)V", lambda o, a: o.fields.update({"title": a[0]}))
        self.m(AL, "<init>", "(Ljava/lang/String;Ljava/lang/String;Ljavax/microedition/lcdui/Image;Ljavax/microedition/lcdui/AlertType;)V",
               lambda o, a: o.fields.update({"title": a[0], "_string": _s(a[1]), "_image": a[2], "_alert_type": a[3]}))
        self.m(AL, "getString", "()Ljava/lang/String;", lambda o, a: o.fields.get("_string"))
        self.m(AL, "setString", "(Ljava/lang/String;)V", lambda o, a: o.fields.__setitem__("_string", _s(a[0])))
        self.m(AL, "getImage", "()Ljavax/microedition/lcdui/Image;", lambda o, a: o.fields.get("_image"))
        self.m(AL, "setImage", "(Ljavax/microedition/lcdui/Image;)V", lambda o, a: o.fields.__setitem__("_image", a[0]))
        self.m(AL, "getType", "()Ljavax/microedition/lcdui/AlertType;", lambda o, a: o.fields.get("_alert_type"))
        self.m(AL, "setType", "(Ljavax/microedition/lcdui/AlertType;)V", lambda o, a: o.fields.__setitem__("_alert_type", a[0]))
        self.m(AL, "getIndicator", "()Ljavax/microedition/lcdui/Gauge;", lambda o, a: o.fields.get("_indicator"))
        self.m(AL, "setIndicator", "(Ljavax/microedition/lcdui/Gauge;)V", lambda o, a: o.fields.__setitem__("_indicator", a[0]))
        self.m(AL, "getTimeout", "()I", lambda o, a: self._alert_get_timeout(o))
        self.m(AL, "setTimeout", "(I)V", lambda o, a: self._alert_set_timeout(o, a[0]))
        self.m(AL, "getDefaultTimeout", "()I", lambda o, a: self._alert_default_timeout(o))

    def _dismiss_command(self):
        if not hasattr(self, "_dismiss_command_singleton"):
            self._dismiss_command_singleton = JavaObject("javax/microedition/lcdui/Command", {"label": "OK", "type": 2, "priority": 0})
        return self._dismiss_command_singleton

    def _alert_default_timeout(self, alert_obj):
        # Real MIDP leaves the default duration implementation-defined
        # ("typically dependent upon the amount of text..."); this scales
        # roughly with message length so short alerts don't linger and long
        # ones don't vanish before they can be read.
        text = alert_obj.fields.get("_string") or ""
        return max(1500, min(8000, 1500 + 40 * len(text)))

    def _alert_get_timeout(self, alert_obj):
        if "_timeout" in alert_obj.fields:
            return alert_obj.fields["_timeout"]
        return self._alert_default_timeout(alert_obj)

    def _alert_set_timeout(self, alert_obj, value):
        if value != ALERT_FOREVER and value < 0:
            self.host.engine_throw("java/lang/IllegalArgumentException", f"bad Alert timeout: {value}")
            return
        alert_obj.fields["_timeout"] = value
        if self.host.current_displayable is alert_obj:
            st = alert_obj.native_companion
            st.generation += 1
            if value != ALERT_FOREVER:
                alert_obj.fields["_dismissed"] = False
                self._alert_start_timer(alert_obj, value, st.generation)

    def _alert_start_timer(self, alert_obj, timeout_ms, generation):
        def worker():
            time.sleep(max(0, timeout_ms) / 1000.0)
            st = alert_obj.native_companion
            if st.generation != generation or alert_obj.fields.get("_dismissed"):
                return  # superseded by a key press or a fresh setCurrent(...) -- nothing to do
            alert_obj.fields["_dismissed"] = True
            nxt = alert_obj.fields.get("_next_displayable")
            if nxt is not None and self.host.current_displayable is alert_obj:
                self.host.set_current(nxt)

        threading.Thread(target=worker, daemon=True).start()

    def alert_dismiss(self, alert_obj, midp_keycode):
        if alert_obj.fields.get("_dismissed"):
            return  # already handled (e.g. the auto-dismiss timer won the race)
        alert_obj.fields["_dismissed"] = True
        alert_obj.native_companion.generation += 1
        left, right = self._soft_key_commands(alert_obj)
        chosen = right if midp_keycode == K.KEY_SOFT_RIGHT else left
        if chosen is None:
            chosen = self._dismiss_command()
        listener = self.host.get_command_listener(alert_obj)
        if listener is not None:
            self.host._safe_call(self.engine.invoke_virtual, listener, listener.class_name, "commandAction",
                                  "(Ljavax/microedition/lcdui/Command;Ljavax/microedition/lcdui/Displayable;)V",
                                  [chosen, alert_obj])
        nxt = alert_obj.fields.get("_next_displayable")
        if nxt is not None and self.host.current_displayable is alert_obj:
            self.host.set_current(nxt)

    def render_alert(self, alert_obj, surface):
        font = self._font_cache.get(None)
        w, h = surface.get_size()
        surface.set_clip(None)
        surface.fill((250, 250, 250))
        pygame.draw.rect(surface, self._alert_bar_color(alert_obj.fields.get("_alert_type")), pygame.Rect(0, 0, w, 6))
        y = 10
        y = self._render_ticker(alert_obj, surface, y, w)
        title = alert_obj.fields.get("title")
        if title:
            surface.blit(font.render(title, True, (0, 0, 0)), (4, y))
            y += font.get_height() + 4
        img = alert_obj.fields.get("_image")
        if isinstance(img, JavaObject) and img.native_companion is not None:
            surface.blit(img.native_companion, (4, y))
            y += img.native_companion.get_height() + 4
        text = alert_obj.fields.get("_string") or ""
        y = self._draw_wrapped_text(surface, font, text, 4, y, max(1, w - 8))
        indicator = alert_obj.fields.get("_indicator")
        if isinstance(indicator, JavaObject):
            y = self._render_form_item(surface, font, indicator, y, w)

    # ---- javax/microedition/lcdui/Ticker ------------------------------------------
    def _reg_ticker(self):
        T = "javax/microedition/lcdui/Ticker"
        self.m(T, "<init>", "(Ljava/lang/String;)V", lambda o, a: o.fields.__setitem__("_text", _s(a[0]) or ""))
        self.m(T, "getString", "()Ljava/lang/String;", lambda o, a: o.fields.get("_text", ""))
        self.m(T, "setString", "(Ljava/lang/String;)V", lambda o, a: o.fields.__setitem__("_text", _s(a[0]) or ""))
        self.superclass_map[T] = "java/lang/Object"

        D = "javax/microedition/lcdui/Displayable"
        self.m(D, "setTicker", "(Ljavax/microedition/lcdui/Ticker;)V", lambda o, a: o.fields.__setitem__("_ticker", a[0]))
        self.m(D, "getTicker", "()Ljavax/microedition/lcdui/Ticker;", lambda o, a: o.fields.get("_ticker"))
        # NOTE: a Ticker attached to a Canvas overlays the top strip (see
        # render_ticker_overlay) rather than shrinking the app's drawable
        # area the way the bottom hotbar does -- real devices reserve space
        # for it, but Canvas + Ticker together is rare enough in practice
        # (Ticker is overwhelmingly used on Form/List/Alert/TextBox menu
        # screens) that the simpler overlay approach is the pragmatic choice.

    # ---- javax/microedition/lcdui/TextBox ------------------------------------------
    def _reg_textbox(self):
        TB = "javax/microedition/lcdui/TextBox"
        self.m(TB, "<init>", "(Ljava/lang/String;Ljava/lang/String;II)V", lambda o, a: self._textbox_init(o, a))
        self.m(TB, "getString", "()Ljava/lang/String;", lambda o, a: o.fields.get("_text", ""))
        self.m(TB, "setString", "(Ljava/lang/String;)V", lambda o, a: self._textbox_set_string(o, _s(a[0]) or ""))
        self.m(TB, "insert", "(Ljava/lang/String;I)V", lambda o, a: self._textbox_insert(o, _s(a[0]) or "", a[1]))
        self.m(TB, "delete", "(II)V", lambda o, a: self._textbox_delete(o, a[0], a[1]))
        self.m(TB, "getMaxSize", "()I", lambda o, a: o.fields.get("_max_size", 0))
        self.m(TB, "setMaxSize", "(I)I", lambda o, a: self._textbox_set_max_size(o, a[0]))
        self.m(TB, "size", "()I", lambda o, a: len(o.fields.get("_text", "")))
        self.m(TB, "getCaretPosition", "()I", lambda o, a: o.fields.get("_cursor", 0))
        self.m(TB, "getConstraints", "()I", lambda o, a: o.fields.get("_constraints", 0))
        self.m(TB, "setConstraints", "(I)V", lambda o, a: o.fields.__setitem__("_constraints", a[0]))
        self.m(TB, "setInitialInputMode", "(Ljava/lang/String;)V", lambda o, a: None)

    def _textbox_init(self, o, a):
        title, text, max_size, constraints = a
        text = _s(text) or ""
        o.fields.update({"title": title, "_text": text, "_max_size": max_size,
                          "_constraints": constraints, "_cursor": len(text)})

    def _textbox_set_string(self, o, text):
        max_size = o.fields.get("_max_size", 0) or 0
        if max_size and len(text) > max_size:
            text = text[:max_size]
        o.fields["_text"] = text
        o.fields["_cursor"] = len(text)

    def _textbox_insert(self, o, text, position):
        cur = o.fields.get("_text", "")
        position = max(0, min(len(cur), position))
        new_text = cur[:position] + text + cur[position:]
        max_size = o.fields.get("_max_size", 0) or 0
        if max_size and len(new_text) > max_size:
            new_text = new_text[:max_size]
        o.fields["_text"] = new_text
        o.fields["_cursor"] = min(len(new_text), position + len(text))

    def _textbox_delete(self, o, offset, length):
        cur = o.fields.get("_text", "")
        offset = max(0, min(len(cur), offset))
        end = max(offset, min(len(cur), offset + length))
        o.fields["_text"] = cur[:offset] + cur[end:]
        o.fields["_cursor"] = min(o.fields.get("_cursor", 0), len(o.fields["_text"]))

    def _textbox_set_max_size(self, o, new_max):
        o.fields["_max_size"] = new_max
        cur = o.fields.get("_text", "")
        if len(cur) > new_max:
            o.fields["_text"] = cur[:new_max]
            o.fields["_cursor"] = min(o.fields.get("_cursor", 0), new_max)
        return new_max

    def _textbox_filter_char(self, constraints, ch):
        base = constraints & 0xFFFF
        if base == 2:    # NUMERIC
            return ch.isdigit() or ch == "-"
        if base == 3:    # PHONENUMBER
            return ch.isdigit() or ch in "+*#- "
        if base == 5:    # DECIMAL
            return ch.isdigit() or ch in ".-"
        return True       # ANY / EMAILADDR / URL: no real device blocks free typing here either

    def textbox_type(self, o, text):
        if not text:
            return
        constraints = o.fields.get("_constraints", 0)
        max_size = o.fields.get("_max_size", 0) or 0
        cur = o.fields.get("_text", "")
        cursor = o.fields.get("_cursor", len(cur))
        accepted = "".join(c for c in text if self._textbox_filter_char(constraints, c))
        if not accepted:
            return
        if max_size and len(cur) + len(accepted) > max_size:
            accepted = accepted[:max(0, max_size - len(cur))]
            if not accepted:
                return
        o.fields["_text"] = cur[:cursor] + accepted + cur[cursor:]
        o.fields["_cursor"] = cursor + len(accepted)

    def textbox_edit_key(self, o, pg_key):
        cur = o.fields.get("_text", "")
        cursor = o.fields.get("_cursor", len(cur))
        if pg_key == pygame.K_BACKSPACE:
            if cursor > 0:
                o.fields["_text"] = cur[:cursor - 1] + cur[cursor:]
                o.fields["_cursor"] = cursor - 1
        elif pg_key == pygame.K_DELETE:
            if cursor < len(cur):
                o.fields["_text"] = cur[:cursor] + cur[cursor + 1:]
        elif pg_key == pygame.K_LEFT:
            o.fields["_cursor"] = max(0, cursor - 1)
        elif pg_key == pygame.K_RIGHT:
            o.fields["_cursor"] = min(len(cur), cursor + 1)

    def render_textbox(self, tb_obj, surface):
        font = self._font_cache.get(None)
        w, h = surface.get_size()
        surface.set_clip(None)
        surface.fill((255, 255, 255))
        y = 2
        y = self._render_ticker(tb_obj, surface, y, w)
        title = tb_obj.fields.get("title")
        if title:
            surface.blit(font.render(title, True, (0, 0, 0)), (4, y))
            y += font.get_height() + 4
            pygame.draw.line(surface, (0, 0, 0), (0, y), (w, y))
            y += 4
        constraints = tb_obj.fields.get("_constraints", 0)
        masked = bool(constraints & 0x10000)
        text = tb_obj.fields.get("_text", "")
        display_text = "*" * len(text) if masked else text
        cursor = tb_obj.fields.get("_cursor", len(text))
        box_rect = pygame.Rect(2, y, max(1, w - 4), max(1, h - y - 18))
        pygame.draw.rect(surface, (245, 245, 245), box_rect)
        pygame.draw.rect(surface, (150, 150, 150), box_rect, 1)
        self._draw_wrapped_text(surface, font, display_text or " ", box_rect.x + 3, box_rect.y + 3, max(1, box_rect.w - 6))
        if int(time.time() * 2) % 2 == 0:  # blink
            cx = box_rect.x + 3 + font.size(display_text[:cursor])[0]
            cy = box_rect.y + 3
            pygame.draw.line(surface, (0, 0, 0), (cx, cy), (cx, cy + font.get_height()))
        max_size = tb_obj.fields.get("_max_size", 0)
        if max_size:
            info = f"{len(text)}/{max_size}"
            rendered = font.render(info, True, (120, 120, 120))
            surface.blit(rendered, (w - rendered.get_width() - 4, h - rendered.get_height() - 2))

    # ---- javax/microedition/rms/RecordStore (persistent save data) -------------
    def _reg_recordstore(self):
        C = "javax/microedition/rms/RecordStore"
        self.m(C, "openRecordStore", "(Ljava/lang/String;Z)Ljavax/microedition/rms/RecordStore;",
               lambda o, a: self._rms_open(_s(a[0])))
        self.m(C, "openRecordStore", "(Ljava/lang/String;ZIZ)Ljavax/microedition/rms/RecordStore;",
               lambda o, a: self._rms_open(_s(a[0])))
        self.m(C, "deleteRecordStore", "(Ljava/lang/String;)V", lambda o, a: self._rms_delete(_s(a[0])))
        self.m(C, "listRecordStores", "()[Ljava/lang/String;", lambda o, a: self._rms_list())
        self.m(C, "closeRecordStore", "()V", lambda o, a: None)
        self.m(C, "getName", "()Ljava/lang/String;", lambda o, a: o.native_companion.name)
        self.m(C, "getNumRecords", "()I", lambda o, a: o.native_companion.num_records())
        self.m(C, "getSizeAvailable", "()I", lambda o, a: 1 << 20)
        self.m(C, "addRecord", "([BII)I", lambda o, a: self._rms_add(o, a))
        self.m(C, "setRecord", "(I[BII)V", lambda o, a: self._rms_set(o, a))
        self.m(C, "getRecord", "(I)[B", lambda o, a: self._rms_get(o, a))
        self.m(C, "getRecordSize", "(I)I", lambda o, a: len(o.native_companion.get_record(a[0])))
        self.m(C, "deleteRecord", "(I)V", lambda o, a: o.native_companion.delete_record(a[0]))
        self.m(C, "enumerateRecords",
               "(Ljavax/microedition/rms/RecordFilter;Ljavax/microedition/rms/RecordComparator;Z)"
               "Ljavax/microedition/rms/RecordEnumeration;",
               lambda o, a: self._rms_enumerate(o))

        E = "javax/microedition/rms/RecordEnumeration"
        self.m(E, "hasNextElement", "()Z", lambda o, a: 1 if o.native_companion["pos"] < len(o.native_companion["ids"]) else 0)
        self.m(E, "nextRecordId", "()I", lambda o, a: self._renum_next(o))
        self.m(E, "nextRecord", "()[B", lambda o, a: self._renum_next_bytes(o))
        self.m(E, "numRecords", "()I", lambda o, a: len(o.native_companion["ids"]))
        self.m(E, "reset", "()V", lambda o, a: o.native_companion.__setitem__("pos", 0))
        self.m(E, "destroy", "()V", lambda o, a: None)
        self.superclass_map[E] = "java/lang/Object"

    def _rms_open(self, name):
        obj = JavaObject("javax/microedition/rms/RecordStore", {})
        obj.native_companion = _RMSStore(self.host.rms_path(name), name)
        return obj

    def _rms_delete(self, name):
        try:
            os_remove = __import__("os").remove
            os_remove(self.host.rms_path(name))
        except OSError:
            pass

    def _rms_list(self):
        import os as _os
        d = self.host.rms_dir
        if not d or not _os.path.isdir(d):
            return JavaArray("Ljava/lang/String;", [])
        names = [f[:-4] for f in _os.listdir(d) if f.endswith(".rms")]
        return JavaArray("Ljava/lang/String;", names)

    def _rms_add(self, o, a):
        data, offset, length = a
        raw = bytes((v & 0xFF) for v in data.values[offset:offset + length])
        return o.native_companion.add_record(raw)

    def _rms_set(self, o, a):
        rid, data, offset, length = a
        raw = bytes((v & 0xFF) for v in data.values[offset:offset + length])
        o.native_companion.set_record(rid, raw)

    def _rms_get(self, o, a):
        raw = o.native_companion.get_record(a[0])
        return JavaArray("B", [b - 256 if b > 127 else b for b in raw])

    def _rms_enumerate(self, o):
        e = JavaObject("javax/microedition/rms/RecordEnumeration", {})
        e.native_companion = {"ids": sorted(o.native_companion.records.keys()), "pos": 0, "store": o.native_companion}
        return e

    def _renum_next(self, o):
        nc = o.native_companion
        rid = nc["ids"][nc["pos"]]
        nc["pos"] += 1
        return rid

    def _renum_next_bytes(self, o):
        nc = o.native_companion
        rid = nc["ids"][nc["pos"]]
        nc["pos"] += 1
        raw = nc["store"].get_record(rid)
        return JavaArray("B", [b - 256 if b > 127 else b for b in raw])

    # ---- common Nokia UI API extensions (com.nokia.mid.ui.*) ------------------
    # Real Nokia Series 40/60 handsets exposed these proprietary extras and a
    # huge number of real-world jars call them; we implement the handful of
    # near-universal ones as safe no-ops/aliases rather than leaving every
    # such jar dead on the first call.
    def _reg_nokia_ui(self):
        self.superclass_map["com/nokia/mid/ui/FullCanvas"] = "javax/microedition/lcdui/Canvas"
        DC = "com/nokia/mid/ui/DeviceControl"
        self.m(DC, "setLights", "(II)V", lambda o, a: None)
        self.m(DC, "isFlashLightLightOn", "()Z", lambda o, a: 0)
        self.m(DC, "startVibra", "(II)V", lambda o, a: None)
        self.m(DC, "stopVibra", "()V", lambda o, a: None)

        DU = "com/nokia/mid/ui/DirectUtils"
        self.m(DU, "createImage", "(III)Ljavax/microedition/lcdui/Image;", lambda o, a: self._du_create_image(a))
        self.m(DU, "createImage", "(Ljava/lang/String;I)Ljavax/microedition/lcdui/Image;",
               lambda o, a: self._img_from_resource([a[0]]))
        self.m(DU, "createImage", "([BIII)Ljavax/microedition/lcdui/Image;",
               lambda o, a: self._img_from_bytes([a[0], a[1], a[2]]))
        self.m(DU, "getDirectGraphics", "(Ljavax/microedition/lcdui/Graphics;)Lcom/nokia/mid/ui/DirectGraphics;",
               lambda o, a: self._du_direct_graphics(a[0]))

        DG = "com/nokia/mid/ui/DirectGraphics"
        self.sf(DG, "FLIP_HORIZONTAL", 8192)
        self.sf(DG, "FLIP_VERTICAL", 16384)
        self.sf(DG, "ROTATE_90", 90)
        self.sf(DG, "ROTATE_180", 180)
        self.sf(DG, "ROTATE_270", 270)
        self.m(DG, "setARGBColor", "(I)V", lambda o, a: self._dg_set_argb(o, a))
        self.m(DG, "getAlphaComponent", "()I", lambda o, a: self._color_alpha(o.native_companion.color))
        self.m(DG, "drawImage", "(Ljavax/microedition/lcdui/Image;IIII)V", lambda o, a: self._dg_draw_image(o, a))
        self.m(DG, "drawPolygon", "([II[IIII)V", lambda o, a: self._dg_polygon(o, a, fill=False))
        self.m(DG, "fillPolygon", "([II[IIII)V", lambda o, a: self._dg_polygon(o, a, fill=True))
        self.m(DG, "drawTriangle", "(IIIIIII)V", lambda o, a: self._dg_triangle(o, a, fill=False))
        self.m(DG, "fillTriangle", "(IIIIIII)V", lambda o, a: self._dg_triangle(o, a, fill=True))
        self.superclass_map[DG] = "java/lang/Object"

    def _color_alpha(self, color):
        return color[3] if len(color) > 3 else 255

    def _du_create_image(self, a):
        w, h, argb = a
        surf = pygame.Surface((max(1, w), max(1, h)), pygame.SRCALPHA)
        surf.fill(_argb_to_rgba(argb))
        return self._wrap_image(surf, mutable=True)

    def _du_direct_graphics(self, graphics_obj):
        companion = self._require(graphics_obj, "Graphics", "DirectUtils.getDirectGraphics")
        dg = JavaObject("com/nokia/mid/ui/DirectGraphics", {})
        dg.native_companion = companion  # same surface/color/clip state
        return dg

    def _dg_set_argb(self, o, a):
        o.native_companion.color = _argb_to_rgba(a[0])

    def _dg_draw_image(self, o, a):
        img, x, y, anchor_and_manip = a[0], a[1], a[2], a[3]
        anchor = anchor_and_manip & 0xFF
        manip = anchor_and_manip & ~0xFF
        gs = o.native_companion
        surf = self._require(img, "Image", "DirectGraphics.drawImage")
        if manip & 8192:  # FLIP_HORIZONTAL
            surf = pygame.transform.flip(surf, True, False)
        if manip & 16384:  # FLIP_VERTICAL
            surf = pygame.transform.flip(surf, False, True)
        rot = manip & 0xFF
        if rot in (90, 180, 270):
            surf = pygame.transform.rotate(surf, -rot)
        w, h = surf.get_size()
        dx, dy = x, y
        if anchor & GFX_HCENTER:
            dx -= w // 2
        elif anchor & GFX_RIGHT:
            dx -= w
        if anchor & GFX_VCENTER:
            dy -= h // 2
        elif anchor & GFX_BOTTOM:
            dy -= h
        px, py = self._gxy(o, dx, dy)
        gs.surface.blit(surf, (px, py))

    def _dg_polygon(self, o, a, fill):
        xs, xoff, ys, yoff, n, argb = a
        pts = [self._gxy(o, xs.values[xoff + i], ys.values[yoff + i]) for i in range(n)]
        gs = o.native_companion
        pygame.draw.polygon(gs.surface, _argb_to_rgba(argb), pts, 0 if fill else 1)

    def _dg_triangle(self, o, a, fill):
        x1, y1, x2, y2, x3, y3, argb = a
        pts = [self._gxy(o, x1, y1), self._gxy(o, x2, y2), self._gxy(o, x3, y3)]
        gs = o.native_companion
        pygame.draw.polygon(gs.surface, _argb_to_rgba(argb), pts, 0 if fill else 1)

    # ---- javax/microedition/lcdui/game (MIDP 2.0 Game API) -----------------------
    # Sprite/TiledLayer are overwhelmingly how real J2ME action games render, so
    # this is a full (if not 100% pixel-perfect on collision) implementation
    # rather than a stub.
    def _reg_game_api(self):
        L = "javax/microedition/lcdui/game/Layer"
        self.m(L, "getX", "()I", lambda o, a: o.native_companion.x)
        self.m(L, "getY", "()I", lambda o, a: o.native_companion.y)
        self.m(L, "getWidth", "()I", lambda o, a: o.native_companion.width)
        self.m(L, "getHeight", "()I", lambda o, a: o.native_companion.height)
        self.m(L, "setPosition", "(II)V", lambda o, a: (setattr(o.native_companion, "x", a[0]), setattr(o.native_companion, "y", a[1])))
        self.m(L, "move", "(II)V", lambda o, a: (setattr(o.native_companion, "x", o.native_companion.x + a[0]),
                                                   setattr(o.native_companion, "y", o.native_companion.y + a[1])))
        self.m(L, "setVisible", "(Z)V", lambda o, a: setattr(o.native_companion, "visible", bool(a[0])))
        self.m(L, "isVisible", "()Z", lambda o, a: 1 if o.native_companion.visible else 0)

        S = "javax/microedition/lcdui/game/Sprite"
        self.sf(S, "TRANS_NONE", 0); self.sf(S, "TRANS_MIRROR_ROT180", 1); self.sf(S, "TRANS_MIRROR", 2)
        self.sf(S, "TRANS_ROT180", 3); self.sf(S, "TRANS_MIRROR_ROT270", 4); self.sf(S, "TRANS_ROT90", 5)
        self.sf(S, "TRANS_ROT270", 6); self.sf(S, "TRANS_MIRROR_ROT90", 7)
        self.m(S, "<init>", "(Ljavax/microedition/lcdui/Image;)V", lambda o, a: self._sprite_init(o, a[0], None, None))
        self.m(S, "<init>", "(Ljavax/microedition/lcdui/Image;II)V", lambda o, a: self._sprite_init(o, a[0], a[1], a[2]))
        self.m(S, "<init>", "(Ljavax/microedition/lcdui/game/Sprite;)V", lambda o, a: self._sprite_copy(o, a[0]))
        self.m(S, "setImage", "(Ljavax/microedition/lcdui/Image;II)V", lambda o, a: self._sprite_init(o, a[0], a[1], a[2]))
        self.m(S, "setFrame", "(I)V", lambda o, a: setattr(o.native_companion, "seq_pos", a[0] % len(o.native_companion.sequence)))
        self.m(S, "getFrame", "()I", lambda o, a: o.native_companion.sequence[o.native_companion.seq_pos])
        self.m(S, "nextFrame", "()V", lambda o, a: self._sprite_step(o, 1))
        self.m(S, "prevFrame", "()V", lambda o, a: self._sprite_step(o, -1))
        self.m(S, "getRawFrameCount", "()I", lambda o, a: len(o.native_companion.frames))
        self.m(S, "getFrameSequenceLength", "()I", lambda o, a: len(o.native_companion.sequence))
        self.m(S, "setFrameSequence", "([I)V", lambda o, a: self._sprite_set_sequence(o, a[0]))
        self.m(S, "setTransform", "(I)V", lambda o, a: setattr(o.native_companion, "transform", a[0]))
        self.m(S, "defineReferencePixel", "(II)V", lambda o, a: (setattr(o.native_companion, "ref_x", a[0]), setattr(o.native_companion, "ref_y", a[1])))
        self.m(S, "getRefPixelX", "()I", lambda o, a: o.native_companion.x + o.native_companion.ref_x)
        self.m(S, "getRefPixelY", "()I", lambda o, a: o.native_companion.y + o.native_companion.ref_y)
        self.m(S, "setRefPixelPosition", "(II)V", lambda o, a: self._sprite_set_ref_pos(o, a))
        self.m(S, "defineCollisionRectangle", "(IIII)V", lambda o, a: setattr(o.native_companion, "coll_rect", tuple(a)))
        self.m(S, "collidesWith", "(Ljavax/microedition/lcdui/game/Sprite;Z)Z", lambda o, a: self._collides(o, a[0]))
        self.m(S, "collidesWith", "(Ljavax/microedition/lcdui/game/TiledLayer;Z)Z", lambda o, a: self._collides(o, a[0]))
        self.m(S, "collidesWith", "(Ljavax/microedition/lcdui/Image;IIZ)Z", lambda o, a: self._collides_image(o, a))
        self.m(S, "paint", "(Ljavax/microedition/lcdui/Graphics;)V", lambda o, a: self._sprite_paint(o, a[0]))

        T = "javax/microedition/lcdui/game/TiledLayer"
        self.m(T, "<init>", "(IILjavax/microedition/lcdui/Image;II)V", lambda o, a: self._tiled_init(o, a))
        self.m(T, "getColumns", "()I", lambda o, a: o.native_companion.cols)
        self.m(T, "getRows", "()I", lambda o, a: o.native_companion.rows)
        self.m(T, "getCellWidth", "()I", lambda o, a: o.native_companion.tile_w)
        self.m(T, "getCellHeight", "()I", lambda o, a: o.native_companion.tile_h)
        self.m(T, "getCell", "(II)I", lambda o, a: o.native_companion.cells[a[1]][a[0]])
        self.m(T, "setCell", "(III)V", lambda o, a: self._tiled_set_cell(o, a))
        self.m(T, "fillCells", "(IIIII)V", lambda o, a: self._tiled_fill(o, a))
        self.m(T, "createAnimatedTile", "(I)I", lambda o, a: self._tiled_create_anim(o, a[0]))
        self.m(T, "setAnimatedTile", "(II)V", lambda o, a: o.native_companion.animated.__setitem__(a[0], a[1]))
        self.m(T, "getAnimatedTile", "(I)I", lambda o, a: o.native_companion.animated.get(a[0], 0))
        self.m(T, "paint", "(Ljavax/microedition/lcdui/Graphics;)V", lambda o, a: self._tiled_paint(o, a[0]))

        LM = "javax/microedition/lcdui/game/LayerManager"
        self.m(LM, "<init>", "()V", lambda o, a: None)
        self.m(LM, "append", "(Ljavax/microedition/lcdui/game/Layer;)V", lambda o, a: o.native_companion.layers.insert(0, a[0]))
        self.m(LM, "insert", "(Ljavax/microedition/lcdui/game/Layer;I)V", lambda o, a: o.native_companion.layers.insert(a[1], a[0]))
        self.m(LM, "remove", "(Ljavax/microedition/lcdui/game/Layer;)V", lambda o, a: self._lm_remove(o, a[0]))
        self.m(LM, "getLayerAt", "(I)Ljavax/microedition/lcdui/game/Layer;", lambda o, a: o.native_companion.layers[a[0]])
        self.m(LM, "getSize", "()I", lambda o, a: len(o.native_companion.layers))
        self.m(LM, "setViewWindow", "(IIII)V", lambda o, a: setattr(o.native_companion, "view", tuple(a)))
        self.m(LM, "paint", "(Ljavax/microedition/lcdui/Graphics;II)V", lambda o, a: self._lm_paint(o, a))

    def _sprite_init(self, o, image, fw, fh):
        st = o.native_companion
        surf = self._require(image, "Image", "Sprite constructor/setImage")
        iw, ih = surf.get_size()
        st.image = surf
        st.frame_w = fw or iw
        st.frame_h = fh or ih
        cols = max(1, iw // st.frame_w)
        rows = max(1, ih // st.frame_h)
        st.frames = [pygame.Rect(c * st.frame_w, r * st.frame_h, st.frame_w, st.frame_h)
                     for r in range(rows) for c in range(cols)]
        st.sequence = list(range(len(st.frames)))
        st.seq_pos = 0
        st.width, st.height = st.frame_w, st.frame_h
        st.coll_rect = (0, 0, st.frame_w, st.frame_h)

    def _sprite_copy(self, o, src):
        if not isinstance(src, JavaObject) or not isinstance(src.native_companion, _SpriteState):
            self.host.engine_throw("java/lang/ClassCastException", "Sprite copy constructor: expected Sprite")
        s = src.native_companion
        o.native_companion = _SpriteState()
        d = o.native_companion
        d.image, d.frame_w, d.frame_h, d.frames = s.image, s.frame_w, s.frame_h, list(s.frames)
        d.sequence, d.seq_pos = list(s.sequence), s.seq_pos
        d.x, d.y, d.width, d.height = s.x, s.y, s.width, s.height
        d.transform, d.ref_x, d.ref_y, d.coll_rect = s.transform, s.ref_x, s.ref_y, s.coll_rect

    def _sprite_step(self, o, delta):
        st = o.native_companion
        st.seq_pos = (st.seq_pos + delta) % len(st.sequence)

    def _sprite_set_sequence(self, o, seq_array):
        st = o.native_companion
        st.sequence = list(seq_array.values) if seq_array is not None else list(range(len(st.frames)))
        st.seq_pos = 0

    def _sprite_set_ref_pos(self, o, a):
        st = o.native_companion
        st.x = a[0] - st.ref_x
        st.y = a[1] - st.ref_y

    def _sprite_paint(self, o, gfx_obj):
        st = o.native_companion
        if not st.visible or st.image is None:
            return
        frame_rect = st.frames[st.sequence[st.seq_pos]]
        sub = st.image.subsurface(frame_rect)
        drawn = _apply_transform(sub, st.transform) if st.transform else sub
        gs = self._require(gfx_obj, "Graphics", "Sprite.paint")
        px, py = self._gxy(gfx_obj, st.x, st.y)
        gs.surface.blit(drawn, (px, py))

    def _rect_for(self, layer_obj):
        st = layer_obj.native_companion
        if isinstance(st, _SpriteState):
            rx, ry, rw, rh = st.coll_rect
            return pygame.Rect(st.x + rx, st.y + ry, rw, rh)
        if isinstance(st, _TiledLayerState):
            return pygame.Rect(st.x, st.y, st.width, st.height)
        return pygame.Rect(0, 0, 0, 0)

    def _collides(self, o, other):
        return 1 if self._rect_for(o).colliderect(self._rect_for(other)) else 0

    def _collides_image(self, o, a):
        img, x, y, _pixel = a
        w, h = self._require(img, "Image", "Sprite.collidesWith(Image)").get_size()
        return 1 if self._rect_for(o).colliderect(pygame.Rect(x, y, w, h)) else 0

    def _tiled_init(self, o, a):
        cols, rows, image, tw, th = a
        st = o.native_companion
        surf = self._require(image, "Image", "TiledLayer constructor")
        st.cols, st.rows, st.tile_w, st.tile_h = cols, rows, tw, th
        st.width, st.height = cols * tw, rows * th
        iw, ih = surf.get_size()
        tcols = max(1, iw // tw)
        trows = max(1, ih // th)
        st.tiles = [None] + [surf.subsurface(pygame.Rect((i % tcols) * tw, (i // tcols) * th, tw, th))
                              for i in range(tcols * trows)]  # index 0 = "empty", tiles are 1-based per spec
        st.cells = [[0] * cols for _ in range(rows)]
        st.animated = {}

    def _tiled_set_cell(self, o, a):
        col, row, tile_index = a
        o.native_companion.cells[row][col] = tile_index

    def _tiled_fill(self, o, a):
        col, row, num_cols, num_rows, tile_index = a
        st = o.native_companion
        for r in range(row, min(row + num_rows, st.rows)):
            for c in range(col, min(col + num_cols, st.cols)):
                st.cells[r][c] = tile_index

    def _tiled_create_anim(self, o, static_index):
        st = o.native_companion
        st.next_anim_id -= 1
        st.animated[st.next_anim_id] = static_index
        return st.next_anim_id

    def _tiled_paint(self, o, gfx_obj):
        st = o.native_companion
        if not st.visible or not st.tiles:
            return
        gs = self._require(gfx_obj, "Graphics", "TiledLayer.paint")
        for r, row in enumerate(st.cells):
            for c, idx in enumerate(row):
                if idx == 0:
                    continue
                real_idx = st.animated.get(idx, idx) if idx < 0 else idx
                if real_idx <= 0 or real_idx >= len(st.tiles):
                    continue
                px, py = self._gxy(gfx_obj, st.x + c * st.tile_w, st.y + r * st.tile_h)
                gs.surface.blit(st.tiles[real_idx], (px, py))

    def _lm_remove(self, o, layer):
        try:
            o.native_companion.layers.remove(layer)
        except ValueError:
            pass

    def _lm_paint(self, o, a):
        gfx_obj, x, y = a
        st = o.native_companion
        gs = self._require(gfx_obj, "Graphics", "LayerManager.paint")
        vx, vy, vw, vh = st.view
        prev_clip = gs.surface.get_clip()
        cx, cy = self._gxy(gfx_obj, x, y)
        gs.surface.set_clip(pygame.Rect(cx, cy, vw, vh))
        # index 0 is topmost (drawn last); iterate back-to-front
        for layer in reversed(st.layers):
            lst = layer.native_companion
            if not getattr(lst, "visible", True):
                continue
            offset_x = x - vx
            offset_y = y - vy
            saved_x, saved_y = lst.x, lst.y
            lst.x += offset_x
            lst.y += offset_y
            try:
                if isinstance(lst, _SpriteState):
                    self._sprite_paint(layer, gfx_obj)
                elif isinstance(lst, _TiledLayerState):
                    self._tiled_paint(layer, gfx_obj)
            finally:
                lst.x, lst.y = saved_x, saved_y
        gs.surface.set_clip(prev_clip)

    # ---- java/io streams (ByteArray/Data In/OutputStream) ------------------------
    def _reg_io(self):
        CLS = "java/lang/Class"
        self.m(CLS, "getResourceAsStream", "(Ljava/lang/String;)Ljava/io/InputStream;",
               lambda o, a: self._get_resource_stream(o, _s(a[0])))
        self.m(CLS, "forName", "(Ljava/lang/String;)Ljava/lang/Class;", lambda o, a: self._class_for_name(_s(a[0])))
        self.m(CLS, "getName", "()Ljava/lang/String;",
               lambda o, a: o.fields.get("name", "").replace("/", "."))
        self.m(CLS, "newInstance", "()Ljava/lang/Object;", lambda o, a: self._class_new_instance(o))

        BIS = "java/io/ByteArrayInputStream"
        self.m(BIS, "<init>", "([B)V", lambda o, a: o.native_companion.reset_from(_bytes_of(a[0])))
        self.m(BIS, "<init>", "([BII)V", lambda o, a: o.native_companion.reset_from(_bytes_of(a[0], a[1], a[2])))

        IS = "java/io/InputStream"
        for C in (IS, BIS, "java/io/DataInputStream"):
            self.m(C, "read", "()I", lambda o, a: o.native_companion.read_one())
            self.m(C, "read", "([B)I", lambda o, a: self._is_read_array(o, a[0], 0, len(a[0].values)))
            self.m(C, "read", "([BII)I", lambda o, a: self._is_read_array(o, a[0], a[1], a[2]))
            self.m(C, "available", "()I", lambda o, a: o.native_companion.available())
            self.m(C, "skip", "(J)J", lambda o, a: o.native_companion.skip(a[0]))
            self.m(C, "close", "()V", lambda o, a: None)
            self.m(C, "mark", "(I)V", lambda o, a: None)
            self.m(C, "reset", "()V", lambda o, a: None)
            self.m(C, "markSupported", "()Z", lambda o, a: 0)

        DIS = "java/io/DataInputStream"
        self.m(DIS, "<init>", "(Ljava/io/InputStream;)V",
               lambda o, a: setattr(o, "native_companion", self._require(a[0], "InputStream", "DataInputStream constructor")))
        self.m(DIS, "readByte", "()B", lambda o, a: _signed8(self._dis_need(o, 1)[0]))
        self.m(DIS, "readUnsignedByte", "()I", lambda o, a: self._dis_need(o, 1)[0])
        self.m(DIS, "readBoolean", "()Z", lambda o, a: 1 if self._dis_need(o, 1)[0] else 0)
        self.m(DIS, "readShort", "()S", lambda o, a: _signed16(int.from_bytes(self._dis_need(o, 2), "big")))
        self.m(DIS, "readUnsignedShort", "()I", lambda o, a: int.from_bytes(self._dis_need(o, 2), "big"))
        self.m(DIS, "readChar", "()C", lambda o, a: int.from_bytes(self._dis_need(o, 2), "big"))
        self.m(DIS, "readInt", "()I", lambda o, a: _signed32(int.from_bytes(self._dis_need(o, 4), "big")))
        self.m(DIS, "readLong", "()J", lambda o, a: _signed64(int.from_bytes(self._dis_need(o, 8), "big")))
        self.m(DIS, "readFloat", "()F", lambda o, a: struct.unpack(">f", self._dis_need(o, 4))[0])
        self.m(DIS, "readDouble", "()D", lambda o, a: struct.unpack(">d", self._dis_need(o, 8))[0])
        self.m(DIS, "readUTF", "()Ljava/lang/String;", lambda o, a: self._dis_read_utf(o))
        self.m(DIS, "readFully", "([B)V", lambda o, a: self._dis_read_fully(o, a[0], 0, len(a[0].values)))
        self.m(DIS, "readFully", "([BII)V", lambda o, a: self._dis_read_fully(o, a[0], a[1], a[2]))
        self.m(DIS, "skipBytes", "(I)I", lambda o, a: o.native_companion.skip(a[0]))

        BOS = "java/io/ByteArrayOutputStream"
        self.m(BOS, "<init>", "()V", lambda o, a: None)
        self.m(BOS, "<init>", "(I)V", lambda o, a: None)
        self.m(BOS, "toByteArray", "()[B", lambda o, a: JavaArray("B", [b - 256 if b > 127 else b for b in o.native_companion.data]))
        self.m(BOS, "size", "()I", lambda o, a: len(o.native_companion.data))
        self.m(BOS, "reset", "()V", lambda o, a: o.native_companion.data.clear())

        OS = "java/io/OutputStream"
        for C in (OS, BOS, "java/io/DataOutputStream"):
            self.m(C, "write", "(I)V", lambda o, a: o.native_companion.data.append(a[0] & 0xFF))
            self.m(C, "write", "([B)V", lambda o, a: o.native_companion.data.extend(b & 0xFF for b in a[0].values))
            self.m(C, "write", "([BII)V", lambda o, a: o.native_companion.data.extend(b & 0xFF for b in a[0].values[a[1]:a[1] + a[2]]))
            self.m(C, "flush", "()V", lambda o, a: None)
            self.m(C, "close", "()V", lambda o, a: None)

        DOS = "java/io/DataOutputStream"
        self.m(DOS, "<init>", "(Ljava/io/OutputStream;)V",
               lambda o, a: setattr(o, "native_companion", self._require(a[0], "OutputStream", "DataOutputStream constructor")))
        self.m(DOS, "writeByte", "(I)V", lambda o, a: o.native_companion.data.append(a[0] & 0xFF))
        self.m(DOS, "writeBoolean", "(Z)V", lambda o, a: o.native_companion.data.append(1 if a[0] else 0))
        self.m(DOS, "writeShort", "(I)V", lambda o, a: o.native_companion.data.extend((a[0] & 0xFFFF).to_bytes(2, "big")))
        self.m(DOS, "writeChar", "(I)V", lambda o, a: o.native_companion.data.extend((a[0] & 0xFFFF).to_bytes(2, "big")))
        self.m(DOS, "writeInt", "(I)V", lambda o, a: o.native_companion.data.extend((a[0] & 0xFFFFFFFF).to_bytes(4, "big")))
        self.m(DOS, "writeLong", "(J)V", lambda o, a: o.native_companion.data.extend((a[0] & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "big")))
        self.m(DOS, "writeFloat", "(F)V", lambda o, a: o.native_companion.data.extend(struct.pack(">f", a[0])))
        self.m(DOS, "writeDouble", "(D)V", lambda o, a: o.native_companion.data.extend(struct.pack(">d", a[0])))
        self.m(DOS, "writeUTF", "(Ljava/lang/String;)V", lambda o, a: self._dos_write_utf(o, _s(a[0])))

    def _class_for_name(self, java_name):
        internal_name = java_name.replace(".", "/")
        if self.cl.has_class(internal_name) or internal_name in self.superclass_map:
            return self.engine.class_object_for(internal_name)
        self.host.log("warn", f"Class.forName: not found: {java_name!r}")
        self.host.engine_throw("java/lang/ClassNotFoundException", java_name)

    def _class_new_instance(self, class_obj):
        internal_name = class_obj.fields.get("name", "")
        obj = self.engine.new_instance(internal_name)
        self.engine.invoke_special(obj, internal_name, "<init>", "()V", [])
        return obj

    def _get_resource_stream(self, class_obj, name):
        resolved = self._resolve_resource_path(class_obj, name)
        data = self.cl.read_resource(resolved)
        if data is None and resolved != name.lstrip("/"):
            data = self.cl.read_resource(name)  # fall back to a root-relative try too
        if data is None:
            similar = self.cl.find_similar_resources(name)
            hint = f" -- similar entries in jar: {similar}" if similar else " -- no similarly-named entries in jar either"
            self.host.log("warn", f"resource not found: {name!r} (resolved to {resolved!r}){hint}")
            return None
        obj = JavaObject("java/io/InputStream", {})
        obj.native_companion = _ByteStream(data)
        return obj

    def _resolve_resource_path(self, class_obj, name):
        if name.startswith("/"):
            return name[1:]
        class_name = class_obj.fields.get("name", "") if isinstance(class_obj, JavaObject) else ""
        pkg = class_name.rsplit("/", 1)[0] if "/" in class_name else ""
        return f"{pkg}/{name}" if pkg else name

    def _is_read_array(self, o, arr, offset, length):
        chunk = o.native_companion.read(length)
        if not chunk:
            return -1
        for i, b in enumerate(chunk):
            arr.values[offset + i] = b - 256 if b > 127 else b
        return len(chunk)

    def _dis_need(self, o, n):
        chunk = o.native_companion.read(n)
        if len(chunk) < n:
            self.host.engine_throw("java/io/EOFException", "")
        return chunk

    def _dis_read_fully(self, o, arr, offset, length):
        chunk = self._dis_need(o, length)
        for i, b in enumerate(chunk):
            arr.values[offset + i] = b - 256 if b > 127 else b

    def _dis_read_utf(self, o):
        n = int.from_bytes(self._dis_need(o, 2), "big")
        raw = self._dis_need(o, n)
        return raw.decode("utf-8", errors="replace")

    def _dos_write_utf(self, o, text):
        raw = text.encode("utf-8")
        o.native_companion.data.extend(len(raw).to_bytes(2, "big"))
        o.native_companion.data.extend(raw)


class _FormState:

    def __init__(self):
        self.items = []  # list[str | Item JavaObject]
        self.cursor = 0     # which item is focused (keyboard navigation -- see form_navigate/form_fire)
        self.scroll_y = 0   # pixel scroll offset so the focused item stays on screen -- see render_form


class _ListState:

    def __init__(self):
        self.list_type = CHOICE_IMPLICIT
        self.items = []             # list[(label, Image|None)]
        self.selected = set()       # indices (MULTIPLE only)
        self.cursor = 0             # currently highlighted row (keyboard navigation)
        self.scroll_row = 0         # first visible row -- see render_list
        self.select_command = None  # List only


class _AlertState:

    def __init__(self):
        self.generation = 0


class _TimerState:

    def __init__(self):
        self.cancelled = False
        self.workers = []


class _SpriteState:

    def __init__(self):
        self.x = 0
        self.y = 0
        self.width = 0
        self.height = 0
        self.visible = True
        self.image = None
        self.frame_w = 0
        self.frame_h = 0
        self.frames = []       # list[pygame.Rect], one per raw frame
        self.sequence = []     # list[int] frame indices, default identity order
        self.seq_pos = 0
        self.transform = 0
        self.ref_x = 0
        self.ref_y = 0
        self.coll_rect = (0, 0, 0, 0)


class _TiledLayerState:

    def __init__(self):
        self.x = 0
        self.y = 0
        self.width = 0
        self.height = 0
        self.visible = True
        self.cols = 0
        self.rows = 0
        self.tile_w = 0
        self.tile_h = 0
        self.tiles = []       # index 0 unused ("empty"), 1..N are the static tile images
        self.cells = []       # [row][col] -> tile index (0 = empty, negative = animated)
        self.animated = {}    # animated tile id (negative) -> current static tile index
        self.next_anim_id = 0


class _LayerManagerState:

    def __init__(self):
        self.layers = []           # index 0 = topmost (painted last)
        self.view = (0, 0, 4096, 4096)  # x, y, w, h -- generous default if setViewWindow() is never called


def _bytes_of(java_array, offset=0, length=None):
    vals = java_array.values
    if length is None:
        length = len(vals) - offset
    return bytes((v & 0xFF) for v in vals[offset:offset + length])


def _signed8(v):
    return v - 256 if v > 127 else v


def _signed16(v):
    return v - 65536 if v > 32767 else v


def _signed32(v):
    return v - 0x100000000 if v & 0x80000000 else v


def _signed64(v):
    return v - 0x10000000000000000 if v & 0x8000000000000000 else v


class _ByteStream:

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def reset_from(self, data: bytes):
        self.data = data
        self.pos = 0

    def read_one(self):
        if self.pos >= len(self.data):
            return -1
        b = self.data[self.pos]
        self.pos += 1
        return b

    def read(self, n):
        chunk = self.data[self.pos:self.pos + max(0, n)]
        self.pos += len(chunk)
        return chunk

    def available(self):
        return max(0, len(self.data) - self.pos)

    def skip(self, n):
        n = min(max(0, n), self.available())
        self.pos += n
        return n


class _ByteSink:

    def __init__(self):
        self.data = bytearray()


class _RMSStore:

    def __init__(self, path, name):
        import pickle
        self._pickle = pickle
        self.path = path
        self.name = name
        self.records = {}
        self.next_id = 1
        self._load()

    def _load(self):
        import os
        if os.path.exists(self.path):
            try:
                with open(self.path, "rb") as f:
                    data = self._pickle.load(f)
                self.records = data.get("records", {})
                self.next_id = data.get("next_id", 1)
            except Exception:
                pass

    def _save(self):
        try:
            with open(self.path, "wb") as f:
                self._pickle.dump({"records": self.records, "next_id": self.next_id}, f)
        except OSError:
            pass

    def add_record(self, raw_bytes):
        rid = self.next_id
        self.next_id += 1
        self.records[rid] = raw_bytes
        self._save()
        return rid

    def set_record(self, rid, raw_bytes):
        self.records[rid] = raw_bytes
        self._save()

    def get_record(self, rid):
        return self.records.get(rid, b"")

    def delete_record(self, rid):
        self.records.pop(rid, None)
        self._save()

    def num_records(self):
        return len(self.records)


def _hkey(v):
    return v if not isinstance(v, JavaObject) else id(v)


def _argb_to_rgba(argb):
    a = (argb >> 24) & 0xFF
    r = (argb >> 16) & 0xFF
    g = (argb >> 8) & 0xFF
    b = argb & 0xFF
    return (r, g, b, a)


def _rgb_from_int(rgb):
    return ((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)


def _int_from_rgb(rgb):
    r, g, b = rgb
    return (r << 16) | (g << 8) | b


def _format_java_float(v):
    if v != v:
        return "NaN"
    if v == float("inf"):
        return "Infinity"
    if v == float("-inf"):
        return "-Infinity"
    av = abs(v)
    if av != 0 and (av >= 1e7 or av < 1e-3):
        # Real Double/Float.toString() switches to scientific notation
        # outside this magnitude range (per the Double.toString javadoc).
        mantissa, exp = f"{v:E}".split("E")
        mantissa = mantissa.rstrip("0").rstrip(".") if "." in mantissa else mantissa
        if "." not in mantissa:
            mantissa += ".0"
        return f"{mantissa}E{int(exp)}"
    if v == int(v) and av < 1e16:
        return f"{v:.1f}"
    return repr(v)


def _apply_transform(surface, transform):
    # javax.microedition.lcdui.Sprite transform constants (TRANS_*):
    # 0 none,1 mirror-rot180,2 mirror,3 rot180,4 mirror-rot270,5 rot90,6 rot270,7 mirror-rot90
    if transform == 0:
        return surface
    if transform == 2:
        return pygame.transform.flip(surface, True, False)
    if transform == 1:
        return pygame.transform.flip(surface, False, True)
    if transform == 3:
        return pygame.transform.rotate(surface, 180)
    if transform == 5:
        return pygame.transform.rotate(surface, -90)
    if transform == 6:
        return pygame.transform.rotate(surface, 90)
    if transform == 4:
        return pygame.transform.flip(pygame.transform.rotate(surface, -90), True, False)
    if transform == 7:
        return pygame.transform.flip(pygame.transform.rotate(surface, 90), True, False)
    return surface
