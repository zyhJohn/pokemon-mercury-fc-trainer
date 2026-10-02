"""Mercury FC names: Latin subset and verified two-byte Chinese glyph IDs.

Chinese IDs enumerate the 6763 GB2312 ideographs, omitting undefined entries,
in groups of 247. Leads skip 06 and 1B. These are game glyph IDs, not GBK bytes.
The source table and Mercury's actual renderer are documented in verified-layout.
"""

ENCODE = {" ": 0, "-": 0xAE, "'": 0xB4}
ENCODE.update({chr(65 + i): 0xBB + i for i in range(26)})
ENCODE.update({chr(97 + i): 0xD5 + i for i in range(26)})
ENCODE.update({str(i): 0xA1 + i for i in range(10)})
DECODE = {value: key for key, value in ENCODE.items()}
CHINESE_LEADS = tuple(value for value in range(1, 31) if value not in (6, 27))


def _chinese_mapping():
    characters = []
    for high in range(0xB0, 0xF8):
        for low in range(0xA1, 0xFF):
            try:
                characters.append(bytes((high, low)).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    if len(characters) != 6763:
        raise RuntimeError("GB2312 ideograph enumeration differs from verified table")
    return {
        char: bytes((CHINESE_LEADS[index // 247], index % 247))
        for index, char in enumerate(characters)
    }


CHINESE_ENCODE = _chinese_mapping()
CHINESE_DECODE = {value: key for key, value in CHINESE_ENCODE.items()}


def decode_name(raw):
    content = raw.split(b"\xff", 1)[0]
    result = []
    index = 0
    while index < len(content):
        lead = content[index]
        if lead in CHINESE_LEADS:
            char = CHINESE_DECODE.get(content[index : index + 2])
            if char is None:
                return None
            index += 2
        else:
            char = DECODE.get(lead)
            if char is None:
                return None
            index += 1
        result.append(char)
    return "".join(result)


def encode_name(text, size, maximum=7):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("姓名不能为空")
    parts = []
    for char in text:
        if char in ENCODE:
            parts.append(bytes((ENCODE[char],)))
        elif char in CHINESE_ENCODE:
            parts.append(CHINESE_ENCODE[char])
        else:
            raise ValueError(
                f"姓名包含未支持字符 {char!r}；支持 GB2312 汉字、英文字母、数字、空格、连字符与英文单引号"
            )
    value = b"".join(parts)
    capacity = min(size, maximum)
    if len(value) > capacity:
        raise ValueError(
            f"姓名最多 {capacity} 字节（汉字占2字节，英文/数字占1字节），不会自动截断"
        )
    return value + b"\xff" * (size - len(value))
