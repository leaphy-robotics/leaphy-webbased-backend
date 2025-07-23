"""General utilities"""


def binary_to_cpp_header(binary_data: bytes, variable_name: str = "variable") -> str:
    """
    Converts binary data into a C++ header file format.

    Args:
        binary_data: The input binary data (as bytes) to be converted.
        variable_name: The desired name for the C++ array variable.

    Returns:
        A string containing the C++ header representation of the binary data.
    """
    header_lines: list[str] = []
    header_lines.append(f"unsigned char {variable_name}[] = {{")

    chunked_data = [binary_data[i : i + 12] for i in range(0, len(binary_data), 12)]

    for index, chunk in enumerate(chunked_data):
        hex_values = ", ".join([f"0x{byte:02x}" for byte in chunk])
        trailing_comma = "," if index < len(chunked_data) - 1 else ""
        header_lines.append(f"  {hex_values}{trailing_comma}")

    header_lines.append("};")
    header_lines.append(f"unsigned int {variable_name}_len = {len(binary_data)};")

    return "\n".join(header_lines)
