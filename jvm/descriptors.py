WIDE_TYPES = ("J", "D")  # long, double occupy two slots


def parse_field_type(desc: str, pos: int = 0):
    c = desc[pos]
    if c in "BCDFIJSZV":
        return c, pos + 1
    if c == "L":
        end = desc.index(";", pos)
        return desc[pos:end + 1], end + 1
    if c == "[":
        inner, next_pos = parse_field_type(desc, pos + 1)
        return "[" + inner, next_pos
    raise ValueError(f"bad type descriptor at {pos}: {desc!r}")


def parse_method_descriptor(desc: str):
    assert desc[0] == "(", desc
    pos = 1
    params = []
    while desc[pos] != ")":
        t, pos = parse_field_type(desc, pos)
        params.append(t)
    ret, _ = parse_field_type(desc, pos + 1)
    return params, ret


def slots_for_type(t: str) -> int:
    return 2 if t in WIDE_TYPES else 1


def is_object_or_array(t: str) -> bool:
    return t.startswith("L") or t.startswith("[")


def default_for_type(t: str):
    if t in ("I", "S", "B", "C", "Z"):
        return 0
    if t == "J":
        return 0
    if t in ("F", "D"):
        return 0.0
    return None
