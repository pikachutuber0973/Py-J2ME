import zipfile
from . import classfile as CF


class JavaObject:
    __slots__ = ("class_name", "fields", "native_companion")

    def __init__(self, class_name, fields=None):
        self.class_name = class_name
        self.fields = fields if fields is not None else {}
        # Optional Python object backing a native class (e.g. a Graphics
        # instance is backed by a real drawing surface). See midp/natives.py
        self.native_companion = None

    def __repr__(self):
        return f"<obj {self.class_name} {id(self) & 0xffff:x}>"


class JavaArray:
    __slots__ = ("elem_type", "values")

    def __init__(self, elem_type, values):
        self.elem_type = elem_type  # e.g. "I", "Ljava/lang/String;", "[I"
        self.values = values

    def __len__(self):
        return len(self.values)

    def __repr__(self):
        return f"<array {self.elem_type}[{len(self.values)}]>"


class JavaThrowable(Exception):

    def __init__(self, java_object):
        self.java_object = java_object
        super().__init__(getattr(java_object, "class_name", "Throwable"))


class ClassNotFoundError(Exception):
    pass


class MethodNotFoundError(Exception):
    pass


class ClassLoader:

    def __init__(self, jar_path=None):
        self.classes = {}          # internal name -> JavaClass
        self._zip = None
        if jar_path:
            self.load_jar(jar_path)

    def load_jar(self, jar_path):
        self._zip = zipfile.ZipFile(jar_path, "r")
        for info in self._zip.infolist():
            if info.filename.endswith(".class"):
                data = self._zip.read(info.filename)
                try:
                    jc = CF.parse_class_bytes(data)
                except CF.ClassFormatError:
                    continue
                self.classes[jc.name] = jc

    def list_class_names(self):
        return sorted(self.classes.keys())

    def list_resource_names(self):
        if not self._zip:
            return []
        return [n for n in self._zip.namelist() if not n.endswith("/")]

    def read_resource(self, name):
        if not self._zip:
            return None
        candidates = [name, name.lstrip("/")]
        names = self._zip.namelist()
        name_set = set(names)
        for c in candidates:
            if c in name_set:
                return self._zip.read(c)
        # Fallback 1: match by filename alone. This mainly helps callers like
        # Image.createImage(String) that -- unlike Class.getResourceAsStream
        # -- have no "calling class" context available to resolve a
        # package-relative path precisely, so an exact-path lookup can miss
        # even when the asset is genuinely present elsewhere in the jar.
        basename = name.rsplit("/", 1)[-1]
        exact_basename_matches = [n for n in names if n.rsplit("/", 1)[-1] == basename] if basename else []
        if len(exact_basename_matches) == 1:
            return self._zip.read(exact_basename_matches[0])
        # Fallback 2: case-insensitive match on the full stripped path, then
        # on basename alone. Some game jars were built with tooling that
        # wasn't consistent about case between the string baked into
        # bytecode and the actual zip entry name.
        stripped_lower = name.lstrip("/").lower()
        for n in names:
            if n.lower() == stripped_lower:
                return self._zip.read(n)
        if basename:
            basename_lower = basename.lower()
            ci_matches = [n for n in names if n.rsplit("/", 1)[-1].lower() == basename_lower]
            if len(ci_matches) == 1:
                return self._zip.read(ci_matches[0])
        return None

    def find_similar_resources(self, name, limit=8):
        if not self._zip:
            return []
        basename = name.rsplit("/", 1)[-1].lower()
        stem = basename.rsplit(".", 1)[0] if "." in basename else basename
        names = self._zip.namelist()
        if stem:
            hits = [n for n in names if stem and stem in n.lower()]
            if hits:
                return hits[:limit]
        return []

    def get_class(self, internal_name):
        jc = self.classes.get(internal_name)
        if jc is None:
            raise ClassNotFoundError(internal_name)
        return jc

    def has_class(self, internal_name):
        return internal_name in self.classes
