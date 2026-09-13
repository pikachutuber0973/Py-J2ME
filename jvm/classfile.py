
import struct
from . import constants as C


class ClassFormatError(Exception):
    pass


class Reader:

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def u1(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u2(self) -> int:
        v = struct.unpack_from(">H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u4(self) -> int:
        v = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def s4(self) -> int:
        v = struct.unpack_from(">i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def s8(self) -> int:
        v = struct.unpack_from(">q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def f4(self) -> float:
        v = struct.unpack_from(">f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def f8(self) -> float:
        v = struct.unpack_from(">d", self.data, self.pos)[0]
        self.pos += 8
        return v

    def bytes(self, n: int) -> bytes:
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v


class CPEntry:
    __slots__ = ("tag", "values")

    def __init__(self, tag, *values):
        self.tag = tag
        self.values = values

    def __repr__(self):
        return f"CPEntry({self.tag}, {self.values})"


class FieldOrMethod:
    def __init__(self, access_flags, name, descriptor, attributes):
        self.access_flags = access_flags
        self.name = name
        self.descriptor = descriptor
        self.attributes = attributes  # name -> raw bytes
        self.code = None  # CodeAttribute, filled in for methods with a body

    @property
    def is_static(self):
        return bool(self.access_flags & C.ACC_STATIC)

    @property
    def is_native(self):
        return bool(self.access_flags & C.ACC_NATIVE)

    @property
    def is_abstract(self):
        return bool(self.access_flags & C.ACC_ABSTRACT)

    def __repr__(self):
        return f"<{self.name}{self.descriptor}>"


class ExceptionTableEntry:
    __slots__ = ("start_pc", "end_pc", "handler_pc", "catch_type")

    def __init__(self, start_pc, end_pc, handler_pc, catch_type):
        self.start_pc = start_pc
        self.end_pc = end_pc
        self.handler_pc = handler_pc
        self.catch_type = catch_type  # class name or None (catch-all/finally)


class CodeAttribute:
    def __init__(self, max_stack, max_locals, code, exception_table):
        self.max_stack = max_stack
        self.max_locals = max_locals
        self.code = code  # raw bytecode bytes
        self.exception_table = exception_table


class JavaClass:

    def __init__(self):
        self.minor_version = 0
        self.major_version = 0
        self.constant_pool = {}  # index -> CPEntry (1-based, JVM style)
        self.access_flags = 0
        self.this_class = None
        self.super_class = None
        self.interfaces = []
        self.fields = []
        self.methods = []
        self.attributes = {}

        # convenience lookup tables built after parsing
        self.methods_by_sig = {}   # "name:descriptor" -> FieldOrMethod
        self.fields_by_name = {}   # "name" -> FieldOrMethod
        self.static_fields = {}    # name -> current value (initialized to defaults)

    # ---- constant pool resolution helpers ----
    def cp(self, index):
        entry = self.constant_pool[index]
        return entry

    def utf8(self, index):
        entry = self.cp(index)
        if entry.tag != C.CONSTANT_Utf8:
            raise ClassFormatError(f"expected Utf8 at #{index}, got tag {entry.tag}")
        return entry.values[0]

    def class_name(self, index):
        entry = self.cp(index)
        if entry.tag != C.CONSTANT_Class:
            raise ClassFormatError(f"expected Class at #{index}")
        return self.utf8(entry.values[0])

    def name_and_type(self, index):
        entry = self.cp(index)
        name = self.utf8(entry.values[0])
        desc = self.utf8(entry.values[1])
        return name, desc

    def resolve_ref(self, index):
        entry = self.cp(index)
        class_idx, nt_idx = entry.values
        cname = self.class_name(class_idx)
        name, desc = self.name_and_type(nt_idx)
        return cname, name, desc

    def loadable_constant(self, index):
        entry = self.cp(index)
        if entry.tag == C.CONSTANT_Integer:
            return entry.values[0]
        if entry.tag == C.CONSTANT_Float:
            return entry.values[0]
        if entry.tag == C.CONSTANT_Long:
            return entry.values[0]
        if entry.tag == C.CONSTANT_Double:
            return entry.values[0]
        if entry.tag == C.CONSTANT_String:
            return self.utf8(entry.values[0])
        if entry.tag == C.CONSTANT_Class:
            return ("class_ref", self.utf8(entry.values[0]))
        raise ClassFormatError(f"unsupported loadable constant tag {entry.tag}")

    @property
    def name(self):
        return self.class_name(self.this_class)

    @property
    def super_name(self):
        return self.class_name(self.super_class) if self.super_class else None

    def __repr__(self):
        return f"<JavaClass {self.name}>"


def _default_value_for_descriptor(desc: str):
    if desc in ("I", "S", "B", "C", "Z"):
        return 0
    if desc == "J":
        return 0
    if desc in ("F", "D"):
        return 0.0
    return None  # object/array reference


def parse_class_bytes(data: bytes) -> JavaClass:
    r = Reader(data)
    magic = r.u4()
    if magic != 0xCAFEBABE:
        raise ClassFormatError(f"bad magic number 0x{magic:08X} - not a .class file")

    jc = JavaClass()
    jc.minor_version = r.u2()
    jc.major_version = r.u2()

    cp_count = r.u2()
    idx = 1
    while idx < cp_count:
        tag = r.u1()
        if tag == C.CONSTANT_Utf8:
            length = r.u2()
            raw = r.bytes(length)
            # MUTF-8 is a superset of ASCII; decode leniently for the
            # (extremely rare in game code) extended forms.
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace")
            jc.constant_pool[idx] = CPEntry(tag, text)
        elif tag == C.CONSTANT_Integer:
            jc.constant_pool[idx] = CPEntry(tag, r.s4())
        elif tag == C.CONSTANT_Float:
            jc.constant_pool[idx] = CPEntry(tag, r.f4())
        elif tag == C.CONSTANT_Long:
            jc.constant_pool[idx] = CPEntry(tag, r.s8())
        elif tag == C.CONSTANT_Double:
            jc.constant_pool[idx] = CPEntry(tag, r.f8())
        elif tag == C.CONSTANT_Class:
            jc.constant_pool[idx] = CPEntry(tag, r.u2())
        elif tag == C.CONSTANT_String:
            jc.constant_pool[idx] = CPEntry(tag, r.u2())
        elif tag in (C.CONSTANT_Fieldref, C.CONSTANT_Methodref, C.CONSTANT_InterfaceMethodref):
            jc.constant_pool[idx] = CPEntry(tag, r.u2(), r.u2())
        elif tag == C.CONSTANT_NameAndType:
            jc.constant_pool[idx] = CPEntry(tag, r.u2(), r.u2())
        elif tag == C.CONSTANT_MethodHandle:
            jc.constant_pool[idx] = CPEntry(tag, r.u1(), r.u2())
        elif tag == C.CONSTANT_MethodType:
            jc.constant_pool[idx] = CPEntry(tag, r.u2())
        elif tag == C.CONSTANT_InvokeDynamic:
            jc.constant_pool[idx] = CPEntry(tag, r.u2(), r.u2())
        else:
            raise ClassFormatError(f"unknown constant pool tag {tag} at #{idx}")

        # Long/Double take two consecutive constant pool slots
        if tag in C.WIDE_ENTRIES:
            idx += 2
        else:
            idx += 1

    jc.access_flags = r.u2()
    jc.this_class = r.u2()
    jc.super_class = r.u2() or None

    iface_count = r.u2()
    jc.interfaces = [r.u2() for _ in range(iface_count)]

    def read_attributes():
        attrs = {}
        count = r.u2()
        for _ in range(count):
            name_idx = r.u2()
            length = r.u4()
            raw = r.bytes(length)
            name = jc.utf8(name_idx)
            attrs[name] = raw
        return attrs

    def read_code_attribute(raw: bytes) -> CodeAttribute:
        cr = Reader(raw)
        max_stack = cr.u2()
        max_locals = cr.u2()
        code_len = cr.u4()
        code = cr.bytes(code_len)
        exc_count = cr.u2()
        exc_table = []
        for _ in range(exc_count):
            start_pc = cr.u2()
            end_pc = cr.u2()
            handler_pc = cr.u2()
            catch_idx = cr.u2()
            catch_type = jc.class_name(catch_idx) if catch_idx else None
            exc_table.append(ExceptionTableEntry(start_pc, end_pc, handler_pc, catch_type))
        # remaining attributes (LineNumberTable, LocalVariableTable, StackMapTable...)
        # are parsed generically but not needed for execution, so skip them.
        return CodeAttribute(max_stack, max_locals, code, exc_table)

    field_count = r.u2()
    for _ in range(field_count):
        af = r.u2()
        name = jc.utf8(r.u2())
        desc = jc.utf8(r.u2())
        attrs = read_attributes()
        fm = FieldOrMethod(af, name, desc, attrs)
        jc.fields.append(fm)
        jc.fields_by_name[name] = fm
        if fm.is_static:
            jc.static_fields[name] = _default_value_for_descriptor(desc)

    method_count = r.u2()
    for _ in range(method_count):
        af = r.u2()
        name = jc.utf8(r.u2())
        desc = jc.utf8(r.u2())
        attrs = read_attributes()
        fm = FieldOrMethod(af, name, desc, attrs)
        if "Code" in attrs:
            fm.code = read_code_attribute(attrs["Code"])
        jc.methods.append(fm)
        jc.methods_by_sig[f"{name}:{desc}"] = fm

    jc.attributes = read_attributes()
    return jc


def parse_class_file(path: str) -> JavaClass:
    with open(path, "rb") as f:
        return parse_class_bytes(f.read())
