MAX_INTEGER_BITS = 4_096
# The largest 4,096-bit value has 1,234 decimal digits.
_MAX_DECIMAL_DIGITS = 1_234
_CHUNK_DIGITS = 9
_DECIMAL_BASE = 10 ** _CHUNK_DIGITS


def parse_decimal(lexeme: str) -> int | None:
    """Convert an unsigned decimal lexeme, or return None if it exceeds the language limit."""
    digits = lexeme.lstrip("0")
    if len(digits) > _MAX_DECIMAL_DIGITS:
        return None
    value = 0
    for start in range(0, len(digits), _CHUNK_DIGITS):
        chunk = digits[start : start + _CHUNK_DIGITS]
        value = value * 10 ** len(chunk) + int(chunk)
    return value if value.bit_length() <= MAX_INTEGER_BITS else None


def format_decimal(value: int) -> str:
    """Format an integer using chunks that fit within Python's decimal conversion limit."""
    magnitude = abs(value)
    chunks: list[int] = []
    while magnitude >= _DECIMAL_BASE:
        magnitude, chunk = divmod(magnitude, _DECIMAL_BASE)
        chunks.append(chunk)
    suffix = "".join(f"{chunk:0{_CHUNK_DIGITS}d}" for chunk in reversed(chunks))
    sign = "-" if value < 0 else ""
    return f"{sign}{magnitude}{suffix}"
