import struct
import math

from . import constants as C
from . import descriptors as D
from .machine import JavaObject, JavaArray, JavaThrowable

# masks for wraparound 32/64 bit integer arithmetic (Java ints/longs wrap)
_I32 = 0xFFFFFFFF
_I64 = 0xFFFFFFFFFFFFFFFF


def _to_i32(v):
    v &= _I32
    return v - 0x100000000 if v & 0x80000000 else v


def _to_i64(v):
    v &= _I64
    return v - 0x10000000000000000 if v & 0x8000000000000000 else v


# A long/double value occupies TWO conceptual slots on the real JVM operand
# stack (unlike every other type, which occupies one) -- this is exactly why
# dup2/pop2/dup2_x1/dup2_x2 have a different, "single wide value" behavior
# form in the spec. We represent that literally: pushing a long/double pushes
# the value *and* this sentinel marking the slot beneath it as its other
# half. That makes the dup2-family opcodes correct automatically, just by
# operating on raw slot counts, with zero special-casing needed for them.
_WIDE_TOP = object()


def _push_wide(stack, value):
    stack.append(value)
    stack.append(_WIDE_TOP)


def _pop_wide(stack):
    stack.pop()  # discard the _WIDE_TOP sentinel
    return stack.pop()


def _push_value(stack, value, type_desc):
    if D.slots_for_type(type_desc) == 2:
        _push_wide(stack, value)
    else:
        stack.append(value)


def _pop_value(stack, type_desc):
    return _pop_wide(stack) if D.slots_for_type(type_desc) == 2 else stack.pop()


def _pop_args(stack, params):
    values = []
    for ptype in reversed(params):
        values.append(_pop_value(stack, ptype))
    values.reverse()
    return values


class Frame:


    def __init__(self, jclass, method, locals_list):
        self.jclass = jclass          # JavaClass the method belongs to (for constant pool)
        self.method = method          # FieldOrMethod with a .code CodeAttribute
        self.locals = locals_list     # list, index-addressed local variable slots
        self.stack = []               # operand stack
        self.pc = 0                   # program counter (index into code bytes)
        self.pc_at_throw = 0          # pc of the instruction currently executing

    def push(self, v):
        self.stack.append(v)

    def pop(self):
        return self.stack.pop()

    def pop_n(self, n):
        vals = self.stack[-n:]
        del self.stack[-n:]
        return vals


class ReturnValue:
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


def run_frame(engine, jclass, method, locals_list):
    code_attr = method.code
    code = code_attr.code
    frame = Frame(jclass, method, locals_list)

    while True:
        try:
            result = _step(engine, frame, code)
        except JavaThrowable as exc:
            handler_pc = _find_handler(engine, jclass, code_attr, frame.pc_at_throw, exc)
            if handler_pc is None:
                raise
            frame.stack.clear()
            frame.push(exc.java_object)
            frame.pc = handler_pc
            continue
        if isinstance(result, ReturnValue):
            return result.value
        # else: fell through to next instruction, loop continues


def _find_handler(engine, jclass, code_attr, pc, exc):
    thrown_name = exc.java_object.class_name if isinstance(exc.java_object, JavaObject) else None
    for entry in code_attr.exception_table:
        if entry.start_pc <= pc < entry.end_pc:
            if entry.catch_type is None:
                return entry.handler_pc
            if thrown_name and engine.is_instance_of(thrown_name, entry.catch_type):
                return entry.handler_pc
    return None


def _step(engine, frame, code):
    start_pc = frame.pc
    frame.pc_at_throw = start_pc  # recorded in case this instruction throws
    op = code[start_pc]
    mnemonic = C.OPCODES.get(op)
    if mnemonic is None:
        raise NotImplementedError(f"unsupported opcode 0x{op:02x} at pc={start_pc} in "
                                   f"{frame.jclass.name}.{frame.method.name}")
    pos = start_pc + 1
    jc = frame.jclass
    stack = frame.stack
    loc = frame.locals

    def u1():
        nonlocal pos
        v = code[pos]; pos += 1; return v

    def s1():
        nonlocal pos
        v = code[pos]; pos += 1
        return v - 256 if v > 127 else v

    def u2():
        nonlocal pos
        v = (code[pos] << 8) | code[pos + 1]; pos += 2; return v

    def s2():
        v = u2()
        return v - 65536 if v > 32767 else v

    def s4():
        nonlocal pos
        v = struct.unpack_from(">i", code, pos)[0]
        pos += 4
        return v

    # ---------------- constants ----------------
    if mnemonic == "nop":
        pass
    elif mnemonic == "aconst_null":
        stack.append(None)
    elif mnemonic.startswith("iconst_"):
        stack.append(int(mnemonic.rsplit("_", 1)[1].replace("m1", "-1")))
    elif mnemonic == "lconst_0":
        _push_wide(stack, 0)
    elif mnemonic == "lconst_1":
        _push_wide(stack, 1)
    elif mnemonic == "fconst_0":
        stack.append(0.0)
    elif mnemonic == "fconst_1":
        stack.append(1.0)
    elif mnemonic == "fconst_2":
        stack.append(2.0)
    elif mnemonic == "dconst_0":
        _push_wide(stack, 0.0)
    elif mnemonic == "dconst_1":
        _push_wide(stack, 1.0)
    elif mnemonic == "bipush":
        stack.append(s1())
    elif mnemonic == "sipush":
        stack.append(s2())
    elif mnemonic in ("ldc", "ldc_w"):
        idx = u1() if mnemonic == "ldc" else u2()
        val = jc.loadable_constant(idx)
        if isinstance(val, tuple) and val[0] == "class_ref":
            val = engine.class_object_for(val[1])
        stack.append(val)
    elif mnemonic == "ldc2_w":
        idx = u2()
        _push_wide(stack, jc.loadable_constant(idx))

    # ---------------- loads ----------------
    elif mnemonic in ("iload", "fload", "aload"):
        stack.append(loc[u1()])
    elif mnemonic in ("lload", "dload"):
        _push_wide(stack, loc[u1()])
    elif mnemonic[:-2].endswith("load") and mnemonic[-1].isdigit():
        # e.g. iload_0, aload_3, lload_0, dload_0
        base, n = mnemonic.rsplit("_", 1)
        if base in ("lload", "dload"):
            _push_wide(stack, loc[int(n)])
        else:
            stack.append(loc[int(n)])
    elif mnemonic in ("iaload", "faload", "aaload", "baload", "caload", "saload"):
        index = stack.pop(); arr = stack.pop()
        _null_check(engine, arr)
        _bounds_check(engine, arr, index)
        stack.append(arr.values[index])
    elif mnemonic in ("laload", "daload"):
        index = stack.pop(); arr = stack.pop()
        _null_check(engine, arr)
        _bounds_check(engine, arr, index)
        _push_wide(stack, arr.values[index])

    # ---------------- stores ----------------
    elif mnemonic in ("istore", "fstore", "astore"):
        idx = u1()
        _store_local(loc, idx, stack.pop())
    elif mnemonic in ("lstore", "dstore"):
        idx = u1()
        _store_local(loc, idx, _pop_wide(stack))
    elif mnemonic[:-2].endswith("store") and mnemonic[-1].isdigit():
        base, n = mnemonic.rsplit("_", 1)
        if base in ("lstore", "dstore"):
            _store_local(loc, int(n), _pop_wide(stack))
        else:
            _store_local(loc, int(n), stack.pop())
    elif mnemonic in ("iastore", "fastore", "aastore", "bastore", "castore", "sastore"):
        val = stack.pop(); index = stack.pop(); arr = stack.pop()
        _null_check(engine, arr)
        _bounds_check(engine, arr, index)
        arr.values[index] = val
    elif mnemonic in ("lastore", "dastore"):
        val = _pop_wide(stack); index = stack.pop(); arr = stack.pop()
        _null_check(engine, arr)
        _bounds_check(engine, arr, index)
        arr.values[index] = val

    # ---------------- stack manipulation ----------------
    elif mnemonic == "pop":
        stack.pop()
    elif mnemonic == "pop2":
        stack.pop(); stack.pop()
    elif mnemonic == "dup":
        stack.append(stack[-1])
    elif mnemonic == "dup_x1":
        a, b = stack.pop(), stack.pop()
        stack.extend([a, b, a])
    elif mnemonic == "dup_x2":
        a, b, c = stack.pop(), stack.pop(), stack.pop()
        stack.extend([a, c, b, a])
    elif mnemonic == "dup2":
        a, b = stack[-2], stack[-1]
        stack.extend([a, b])
    elif mnemonic == "dup2_x1":
        a, b, c = stack.pop(), stack.pop(), stack.pop()
        stack.extend([b, a, c, b, a])
    elif mnemonic == "dup2_x2":
        a, b, c, d = stack.pop(), stack.pop(), stack.pop(), stack.pop()
        stack.extend([b, a, d, c, b, a])
    elif mnemonic == "swap":
        a, b = stack.pop(), stack.pop()
        stack.extend([a, b])

    # ---------------- arithmetic ----------------
    elif mnemonic == "iadd":
        b, a = stack.pop(), stack.pop(); stack.append(_to_i32(a + b))
    elif mnemonic == "ladd":
        b, a = _pop_wide(stack), _pop_wide(stack); _push_wide(stack, _to_i64(a + b))
    elif mnemonic == "fadd":
        b, a = stack.pop(), stack.pop(); stack.append(a + b)
    elif mnemonic == "dadd":
        b, a = _pop_wide(stack), _pop_wide(stack); _push_wide(stack, a + b)
    elif mnemonic == "isub":
        b, a = stack.pop(), stack.pop(); stack.append(_to_i32(a - b))
    elif mnemonic == "lsub":
        b, a = _pop_wide(stack), _pop_wide(stack); _push_wide(stack, _to_i64(a - b))
    elif mnemonic == "fsub":
        b, a = stack.pop(), stack.pop(); stack.append(a - b)
    elif mnemonic == "dsub":
        b, a = _pop_wide(stack), _pop_wide(stack); _push_wide(stack, a - b)
    elif mnemonic == "imul":
        b, a = stack.pop(), stack.pop(); stack.append(_to_i32(a * b))
    elif mnemonic == "lmul":
        b, a = _pop_wide(stack), _pop_wide(stack); _push_wide(stack, _to_i64(a * b))
    elif mnemonic == "fmul":
        b, a = stack.pop(), stack.pop(); stack.append(a * b)
    elif mnemonic == "dmul":
        b, a = _pop_wide(stack), _pop_wide(stack); _push_wide(stack, a * b)
    elif mnemonic == "idiv":
        b, a = stack.pop(), stack.pop()
        if b == 0:
            engine.throw("java/lang/ArithmeticException", "/ by zero")
        stack.append(_to_i32(_java_int_div(a, b)))
    elif mnemonic == "ldiv":
        b, a = _pop_wide(stack), _pop_wide(stack)
        if b == 0:
            engine.throw("java/lang/ArithmeticException", "/ by zero")
        _push_wide(stack, _to_i64(_java_int_div(a, b)))
    elif mnemonic == "fdiv":
        b, a = stack.pop(), stack.pop()
        stack.append(a / b if b != 0 else (float("inf") if a > 0 else float("-inf") if a < 0 else float("nan")))
    elif mnemonic == "ddiv":
        b, a = _pop_wide(stack), _pop_wide(stack)
        _push_wide(stack, a / b if b != 0 else (float("inf") if a > 0 else float("-inf") if a < 0 else float("nan")))
    elif mnemonic == "irem":
        b, a = stack.pop(), stack.pop()
        if b == 0:
            engine.throw("java/lang/ArithmeticException", "/ by zero")
        stack.append(_to_i32(_java_rem(a, b)))
    elif mnemonic == "lrem":
        b, a = _pop_wide(stack), _pop_wide(stack)
        if b == 0:
            engine.throw("java/lang/ArithmeticException", "/ by zero")
        _push_wide(stack, _to_i64(_java_rem(a, b)))
    elif mnemonic == "frem":
        b, a = stack.pop(), stack.pop()
        stack.append(math.fmod(a, b) if b else float("nan"))
    elif mnemonic == "drem":
        b, a = _pop_wide(stack), _pop_wide(stack)
        _push_wide(stack, math.fmod(a, b) if b else float("nan"))
    elif mnemonic == "ineg":
        stack.append(_to_i32(-stack.pop()))
    elif mnemonic == "lneg":
        _push_wide(stack, _to_i64(-_pop_wide(stack)))
    elif mnemonic == "fneg":
        stack.append(-stack.pop())
    elif mnemonic == "dneg":
        _push_wide(stack, -_pop_wide(stack))
    elif mnemonic == "ishl":
        b, a = stack.pop(), stack.pop(); stack.append(_to_i32(a << (b & 0x1F)))
    elif mnemonic == "lshl":
        b = stack.pop(); a = _pop_wide(stack); _push_wide(stack, _to_i64(a << (b & 0x3F)))
    elif mnemonic == "ishr":
        b, a = stack.pop(), stack.pop(); stack.append(_to_i32(a >> (b & 0x1F)))
    elif mnemonic == "lshr":
        b = stack.pop(); a = _pop_wide(stack); _push_wide(stack, _to_i64(a >> (b & 0x3F)))
    elif mnemonic == "iushr":
        b, a = stack.pop(), stack.pop(); stack.append(_to_i32((a & _I32) >> (b & 0x1F)))
    elif mnemonic == "lushr":
        b = stack.pop(); a = _pop_wide(stack); _push_wide(stack, _to_i64((a & _I64) >> (b & 0x3F)))
    elif mnemonic == "iand":
        b, a = stack.pop(), stack.pop(); stack.append(_to_i32(a & b))
    elif mnemonic == "land":
        b, a = _pop_wide(stack), _pop_wide(stack); _push_wide(stack, _to_i64(a & b))
    elif mnemonic == "ior":
        b, a = stack.pop(), stack.pop(); stack.append(_to_i32(a | b))
    elif mnemonic == "lor":
        b, a = _pop_wide(stack), _pop_wide(stack); _push_wide(stack, _to_i64(a | b))
    elif mnemonic == "ixor":
        b, a = stack.pop(), stack.pop(); stack.append(_to_i32(a ^ b))
    elif mnemonic == "lxor":
        b, a = _pop_wide(stack), _pop_wide(stack); _push_wide(stack, _to_i64(a ^ b))
    elif mnemonic == "iinc":
        idx = u1(); delta = s1()
        loc[idx] = _to_i32(loc[idx] + delta)

    # ---------------- conversions ----------------
    elif mnemonic == "i2l":
        _push_wide(stack, stack.pop())
    elif mnemonic == "i2f":
        stack.append(float(stack.pop()))
    elif mnemonic == "i2d":
        _push_wide(stack, float(stack.pop()))
    elif mnemonic == "l2i":
        stack.append(_to_i32(_pop_wide(stack)))
    elif mnemonic == "l2f":
        stack.append(float(_pop_wide(stack)))
    elif mnemonic == "l2d":
        _push_wide(stack, float(_pop_wide(stack)))
    elif mnemonic == "f2i":
        v = stack.pop(); stack.append(_to_i32(int(v)) if v == v and abs(v) != float("inf") else 0)
    elif mnemonic == "d2i":
        v = _pop_wide(stack); stack.append(_to_i32(int(v)) if v == v and abs(v) != float("inf") else 0)
    elif mnemonic == "f2l":
        v = stack.pop(); _push_wide(stack, _to_i64(int(v)) if v == v and abs(v) != float("inf") else 0)
    elif mnemonic == "d2l":
        v = _pop_wide(stack); _push_wide(stack, _to_i64(int(v)) if v == v and abs(v) != float("inf") else 0)
    elif mnemonic == "f2d":
        _push_wide(stack, stack.pop())
    elif mnemonic == "d2f":
        stack.append(_pop_wide(stack))
    elif mnemonic == "i2b":
        v = stack.pop() & 0xFF; stack.append(v - 256 if v > 127 else v)
    elif mnemonic == "i2c":
        stack.append(stack.pop() & 0xFFFF)
    elif mnemonic == "i2s":
        v = stack.pop() & 0xFFFF; stack.append(v - 65536 if v > 32767 else v)

    # ---------------- comparisons ----------------
    elif mnemonic == "lcmp":
        b, a = _pop_wide(stack), _pop_wide(stack); stack.append(1 if a > b else (-1 if a < b else 0))
    elif mnemonic in ("fcmpl", "fcmpg"):
        b, a = stack.pop(), stack.pop()
        if a != a or b != b:  # NaN
            stack.append(1 if mnemonic.endswith("g") else -1)
        else:
            stack.append(1 if a > b else (-1 if a < b else 0))
    elif mnemonic in ("dcmpl", "dcmpg"):
        b, a = _pop_wide(stack), _pop_wide(stack)
        if a != a or b != b:  # NaN
            stack.append(1 if mnemonic.endswith("g") else -1)
        else:
            stack.append(1 if a > b else (-1 if a < b else 0))

    # ---------------- control flow ----------------
    elif mnemonic in ("ifeq", "ifne", "iflt", "ifge", "ifgt", "ifle"):
        off = s2(); v = stack.pop()
        if _cmp0(mnemonic[2:], v):
            pos = start_pc + off
    elif mnemonic.startswith("if_icmp"):
        off = s2(); b, a = stack.pop(), stack.pop()
        if _cmp2(mnemonic[len("if_icmp"):], a, b):
            pos = start_pc + off
    elif mnemonic in ("if_acmpeq", "if_acmpne"):
        off = s2(); b, a = stack.pop(), stack.pop()
        eq = (a is b) or (a == b if (a is not None and b is not None) else a is b)
        if (mnemonic == "if_acmpeq") == eq:
            pos = start_pc + off
    elif mnemonic == "ifnull":
        off = s2()
        if stack.pop() is None:
            pos = start_pc + off
    elif mnemonic == "ifnonnull":
        off = s2()
        if stack.pop() is not None:
            pos = start_pc + off
    elif mnemonic == "goto":
        off = s2(); pos = start_pc + off
    elif mnemonic == "goto_w":
        off = s4(); pos = start_pc + off
    elif mnemonic == "jsr":
        off = s2(); stack.append(pos); pos = start_pc + off
    elif mnemonic == "jsr_w":
        off = s4(); stack.append(pos); pos = start_pc + off
    elif mnemonic == "ret":
        pos = loc[u1()]
    elif mnemonic == "tableswitch":
        pos = _pad4(pos, start_pc)
        default = s4(); low = s4(); high = s4()
        index = stack.pop()
        if low <= index <= high:
            offs_pos = pos + (index - low) * 4
            off = struct.unpack_from(">i", code, offs_pos)[0]
            pos = start_pc + off
        else:
            pos = start_pc + default
    elif mnemonic == "lookupswitch":
        pos = _pad4(pos, start_pc)
        default = s4(); npairs = s4()
        target = None
        for i in range(npairs):
            key = struct.unpack_from(">i", code, pos)[0]
            off = struct.unpack_from(">i", code, pos + 4)[0]
            pos += 8
            if key == stack[-1]:
                target = off
        stack.pop()
        pos = start_pc + (target if target is not None else default)

    # ---------------- returns ----------------
    elif mnemonic == "return":
        frame.pc = pos
        return ReturnValue(None)
    elif mnemonic in ("ireturn", "freturn", "areturn"):
        v = stack.pop()
        frame.pc = pos
        return ReturnValue(v)
    elif mnemonic in ("lreturn", "dreturn"):
        v = _pop_wide(stack)
        frame.pc = pos
        return ReturnValue(v)

    # ---------------- fields ----------------
    elif mnemonic == "getstatic":
        idx = u2(); cname, name, desc = jc.resolve_ref(idx)
        _push_value(stack, engine.get_static(cname, name, desc), desc)
    elif mnemonic == "putstatic":
        idx = u2(); cname, name, desc = jc.resolve_ref(idx)
        engine.put_static(cname, name, desc, _pop_value(stack, desc))
    elif mnemonic == "getfield":
        idx = u2(); cname, name, desc = jc.resolve_ref(idx)
        obj = stack.pop()
        _null_check(engine, obj)
        _push_value(stack, engine.get_field(obj, cname, name, desc), desc)
    elif mnemonic == "putfield":
        idx = u2(); cname, name, desc = jc.resolve_ref(idx)
        val = _pop_value(stack, desc); obj = stack.pop()
        _null_check(engine, obj)
        engine.put_field(obj, cname, name, desc, val)

    # ---------------- invocation ----------------
    elif mnemonic == "invokestatic":
        idx = u2(); cname, name, desc = jc.resolve_ref(idx)
        params, ret = D.parse_method_descriptor(desc)
        args = _pop_args(stack, params)
        result = engine.invoke_static(cname, name, desc, args)
        if ret != "V":
            _push_value(stack, result, ret)
    elif mnemonic in ("invokevirtual", "invokeinterface"):
        idx = u2()
        if mnemonic == "invokeinterface":
            u1(); u1()  # count, 0 (unused historical operands)
        cname, name, desc = jc.resolve_ref(idx)
        params, ret = D.parse_method_descriptor(desc)
        args = _pop_args(stack, params)
        obj = stack.pop()
        _null_check(engine, obj)
        result = engine.invoke_virtual(obj, cname, name, desc, args)
        if ret != "V":
            _push_value(stack, result, ret)
    elif mnemonic == "invokespecial":
        idx = u2(); cname, name, desc = jc.resolve_ref(idx)
        params, ret = D.parse_method_descriptor(desc)
        args = _pop_args(stack, params)
        obj = stack.pop()
        _null_check(engine, obj)
        result = engine.invoke_special(obj, cname, name, desc, args)
        if ret != "V":
            _push_value(stack, result, ret)
    elif mnemonic == "invokedynamic":
        raise NotImplementedError("invokedynamic (lambdas) are not part of the CLDC/MIDP "
                                   "bytecode profile this emulator targets")

    # ---------------- objects / arrays ----------------
    elif mnemonic == "new":
        idx = u2(); cname = jc.class_name(idx)
        stack.append(engine.new_instance(cname))
    elif mnemonic == "newarray":
        atype = u1(); count = stack.pop()
        if count < 0:
            engine.throw("java/lang/NegativeArraySizeException", str(count))
        default = 0 if atype != C.T_FLOAT and atype != C.T_DOUBLE else 0.0
        stack.append(JavaArray(C.ARRAY_TYPE_NAMES[atype], [default] * count))
    elif mnemonic == "anewarray":
        idx = u2(); cname = jc.class_name(idx); count = stack.pop()
        if count < 0:
            engine.throw("java/lang/NegativeArraySizeException", str(count))
        stack.append(JavaArray("L" + cname + ";", [None] * count))
    elif mnemonic == "multianewarray":
        idx = u2(); cname = jc.class_name(idx); dims = u1()
        counts = _pop_n(stack, dims)
        stack.append(_make_multiarray(cname, counts))
    elif mnemonic == "arraylength":
        arr = stack.pop()
        _null_check(engine, arr)
        stack.append(len(arr.values))
    elif mnemonic == "athrow":
        obj = stack.pop()
        _null_check(engine, obj)
        frame.pc = pos
        raise JavaThrowable(obj)
    elif mnemonic == "checkcast":
        idx = u2(); cname = jc.class_name(idx)
        obj = stack[-1]
        if obj is not None and isinstance(obj, JavaObject) and not engine.is_instance_of(obj.class_name, cname):
            engine.throw("java/lang/ClassCastException", f"{obj.class_name} cannot be cast to {cname}")
    elif mnemonic == "instanceof":
        idx = u2(); cname = jc.class_name(idx)
        obj = stack.pop()
        if obj is None:
            stack.append(0)
        elif isinstance(obj, JavaObject):
            stack.append(1 if engine.is_instance_of(obj.class_name, cname) else 0)
        else:
            stack.append(1)
    elif mnemonic in ("monitorenter", "monitorexit"):
        stack.pop()  # single-threaded interpreter: locks are a no-op
    elif mnemonic == "wide":
        pos = _do_wide(code, pos, loc, stack)

    else:
        raise NotImplementedError(f"opcode '{mnemonic}' not implemented")

    frame.pc = pos
    return None


def _pop_n(stack, n):
    if n == 0:
        return []
    vals = stack[-n:]
    del stack[-n:]
    return vals


def _store_local(loc, idx, val):
    while len(loc) <= idx:
        loc.append(0)
    loc[idx] = val


def _cmp0(op, v):
    return {"eq": v == 0, "ne": v != 0, "lt": v < 0, "ge": v >= 0, "gt": v > 0, "le": v <= 0}[op]


def _cmp2(op, a, b):
    return {"eq": a == b, "ne": a != b, "lt": a < b, "ge": a >= b, "gt": a > b, "le": a <= b}[op]


def _java_int_div(a, b):
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _java_rem(a, b):
    return a - b * _java_int_div(a, b)


def _pad4(pos, start_pc):
    # tableswitch/lookupswitch operands are aligned to a 4-byte boundary
    # measured from the start of the method's bytecode array.
    while pos % 4 != 0:
        pos += 1
    return pos


def _make_multiarray(elem_class, counts):
    if len(counts) == 1:
        return JavaArray("L" + elem_class + ";", [None] * counts[0])
    n = counts[0]
    return JavaArray("[" * (len(counts) - 1) + "L" + elem_class + ";",
                      [_make_multiarray(elem_class, counts[1:]) for _ in range(n)])


def _null_check(engine, obj):
    if obj is None:
        engine.throw("java/lang/NullPointerException", "")


def _bounds_check(engine, arr, index):
    if index < 0 or index >= len(arr.values):
        engine.throw("java/lang/ArrayIndexOutOfBoundsException", str(index))


def _do_wide(code, pos, loc, stack):
    sub_op = code[pos]; pos += 1
    sub_mnemonic = C.OPCODES[sub_op]
    idx = (code[pos] << 8) | code[pos + 1]; pos += 2
    if sub_mnemonic == "iinc":
        delta = struct.unpack_from(">h", code, pos)[0]; pos += 2
        loc[idx] = _to_i32(loc[idx] + delta)
    elif sub_mnemonic in ("istore", "fstore", "astore"):
        _store_local(loc, idx, stack.pop())
    elif sub_mnemonic in ("lstore", "dstore"):
        _store_local(loc, idx, _pop_wide(stack))
    elif sub_mnemonic in ("iload", "fload", "aload"):
        stack.append(loc[idx])
    elif sub_mnemonic in ("lload", "dload"):
        _push_wide(stack, loc[idx])
    elif sub_mnemonic == "ret":
        pos = loc[idx]
    return pos
