"""General utilities"""


def bin2header(data, var_name="var"):
    """Takes binary data and converts it to c++ headers"""
    out = []
    out.append(f"unsigned char {var_name}[] = {{")
    l = [data[i : i + 12] for i in range(0, len(data), 12)]
    for i, x in enumerate(l):
        line = ", ".join([f"0x{c:02x}" for c in x])
        end_comma = "," if i < len(l) - 1 else ""
        out.append(f"  {line}{end_comma}")
    out.append("};")
    out.append(f"unsigned int {var_name}_len = {len(data)};")
    return "\n".join(out)
