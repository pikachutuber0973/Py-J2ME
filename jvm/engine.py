import threading

from . import interpreter
from . import descriptors as D
from .machine import ClassLoader, JavaObject, JavaArray, JavaThrowable, MethodNotFoundError


class Engine:
    def __init__(self, classloader: ClassLoader, native_bridge):
        self.cl = classloader
        self.natives = native_bridge
        self.natives.engine = self
        self.initialized_classes = set()
        self._class_objects = {}
        self.log = []  # (level, message) tuples for the GUI console panel
        self._call_stacks = threading.local()  # per-thread: list of "Class.method(desc)" frames,
                                                 # for diagnostics (see call_stack_snapshot)

    def call_stack_snapshot(self, limit=6):        return list(getattr(self._call_stacks, "frames", []))[-limit:]

    # ---------------------------------------------------------------
    # class hierarchy helpers
    # ---------------------------------------------------------------
    def _interpreted_ancestors(self, class_name):
        result = []
        cur = class_name
        while cur and self.cl.has_class(cur):
            result.append(cur)
            cur = self.cl.get_class(cur).super_name
        return result

    def _native_boundary(self, class_name):
        cur = class_name
        while cur and self.cl.has_class(cur):
            cur = self.cl.get_class(cur).super_name
        return cur

    def is_instance_of(self, class_name, target_name):
        if class_name == target_name:
            return True
        if self.cl.has_class(class_name):
            jc = self.cl.get_class(class_name)
            for iface_idx in jc.interfaces:
                if self.is_instance_of(jc.class_name(iface_idx), target_name):
                    return True
            if jc.super_name and self.is_instance_of(jc.super_name, target_name):
                return True
            return False
        return self.natives.is_instance_of(class_name, target_name)

    # ---------------------------------------------------------------
    # class initialization
    # ---------------------------------------------------------------
    def ensure_clinit(self, class_name):
        if class_name in self.initialized_classes:
            return
        self.initialized_classes.add(class_name)
        if self.cl.has_class(class_name):
            jc = self.cl.get_class(class_name)
            if jc.super_name:
                self.ensure_clinit(jc.super_name)
            m = jc.methods_by_sig.get("<clinit>:()V")
            if m and m.code:
                self._run_interpreted(jc, m, None, [])
        else:
            self.natives.ensure_static_init(class_name)

    # ---------------------------------------------------------------
    # object / array creation
    # ---------------------------------------------------------------
    def new_instance(self, class_name):
        self.ensure_clinit(class_name)
        obj = JavaObject(class_name)
        for cn in self._interpreted_ancestors(class_name):
            jc = self.cl.get_class(cn)
            for f in jc.fields:
                if not f.is_static and f.name not in obj.fields:
                    obj.fields[f.name] = D.default_for_type(f.descriptor)
        boundary = self._native_boundary(class_name)
        if boundary:
            self.natives.instantiate(obj, boundary)
        return obj

    def class_object_for(self, class_name):
        if class_name not in self._class_objects:
            co = JavaObject("java/lang/Class", {"name": class_name})
            self._class_objects[class_name] = co
        return self._class_objects[class_name]

    # ---------------------------------------------------------------
    # method invocation
    # ---------------------------------------------------------------
    def invoke_static(self, owner, name, desc, args):
        self.ensure_clinit(owner)
        if self.cl.has_class(owner):
            jc = self.cl.get_class(owner)
            m = jc.methods_by_sig.get(f"{name}:{desc}")
            if m and m.code:
                return self._run_interpreted(jc, m, None, args)
        return self.natives.call(owner, name, desc, None, args)

    def invoke_special(self, obj, owner, name, desc, args):
        if self.cl.has_class(owner):
            jc = self.cl.get_class(owner)
            m = jc.methods_by_sig.get(f"{name}:{desc}")
            if m and m.code:
                self.ensure_clinit(owner)
                return self._run_interpreted(jc, m, obj, args)
        return self.natives.call(owner, name, desc, obj, args)

    def invoke_virtual(self, obj, static_owner, name, desc, args):
        runtime_class = obj.class_name if isinstance(obj, JavaObject) else static_owner
        cur = runtime_class
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            if self.cl.has_class(cur):
                jc = self.cl.get_class(cur)
                m = jc.methods_by_sig.get(f"{name}:{desc}")
                if m and m.code:
                    self.ensure_clinit(cur)
                    return self._run_interpreted(jc, m, obj, args)
                cur = jc.super_name or "java/lang/Object"
            else:
                return self.natives.call(cur, name, desc, obj, args)
        return self.natives.call("java/lang/Object", name, desc, obj, args)

    def _run_interpreted(self, jc, method, this_obj, args):
        if method.code is None:
            return self.natives.call(jc.name, method.name, method.descriptor, this_obj, args)
        params, _ret = D.parse_method_descriptor(method.descriptor)
        locals_list = []
        if not method.is_static:
            locals_list.append(this_obj)
        for ptype, val in zip(params, args):
            locals_list.append(val)
            if D.slots_for_type(ptype) == 2:
                locals_list.append(0)
        max_locals = method.code.max_locals
        while len(locals_list) < max_locals:
            locals_list.append(0)
        frames = getattr(self._call_stacks, "frames", None)
        if frames is None:
            frames = []
            self._call_stacks.frames = frames
        frames.append(f"{jc.name}.{method.name}{method.descriptor}")
        try:
            return interpreter.run_frame(self, jc, method, locals_list)
        finally:
            frames.pop()

    # ---------------------------------------------------------------
    # fields
    # ---------------------------------------------------------------
    def get_static(self, owner, name, desc):
        self.ensure_clinit(owner)
        if self.cl.has_class(owner):
            cur = owner
            while cur and self.cl.has_class(cur):
                cjc = self.cl.get_class(cur)
                if name in cjc.static_fields:
                    return cjc.static_fields[name]
                cur = cjc.super_name
            return None
        return self.natives.get_static_field(owner, name, desc)

    def put_static(self, owner, name, desc, value):
        self.ensure_clinit(owner)
        if self.cl.has_class(owner):
            cur = owner
            while cur and self.cl.has_class(cur):
                cjc = self.cl.get_class(cur)
                if name in cjc.static_fields:
                    cjc.static_fields[name] = value
                    return
                cur = cjc.super_name
            self.cl.get_class(owner).static_fields[name] = value
            return
        self.natives.put_static_field(owner, name, desc, value)

    def get_field(self, obj, owner, name, desc):
        if isinstance(obj, JavaObject) and name in obj.fields:
            return obj.fields.get(name)
        return self.natives.get_instance_field(obj, owner, name, desc)

    def put_field(self, obj, owner, name, desc, value):
        if isinstance(obj, JavaObject):
            obj.fields[name] = value
        else:
            self.natives.put_instance_field(obj, owner, name, desc, value)

    # ---------------------------------------------------------------
    # exceptions
    # ---------------------------------------------------------------
    def throw(self, class_name, message):
        obj = JavaObject(class_name, {"message": message})
        raise JavaThrowable(obj)

    def make_string(self, pyvalue: str):
        return pyvalue

    # ---------------------------------------------------------------
    # entry point helper: call a method by simple dotted name from outside
    # ---------------------------------------------------------------
    def call_entrypoint(self, class_name, method_name, descriptor, this_obj=None, args=None):
        args = args or []
        jc = self.cl.get_class(class_name)
        m = jc.methods_by_sig.get(f"{method_name}:{descriptor}")
        if not m:
            raise MethodNotFoundError(f"{class_name}.{method_name}{descriptor} not found")
        self.ensure_clinit(class_name)
        return self._run_interpreted(jc, m, this_obj, args)
