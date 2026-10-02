"""Verified single-byte name subset; unsupported encodings remain untouched.

Base engine mapping: pret/pokefirered charmap.txt. Local trainer sample ee ed dc
uses this subset. Chinese glyph encoding is deliberately not guessed.
"""

ENCODE = {" ": 0, "-": 0xAE, "'": 0xB4}
ENCODE.update({chr(65 + i): 0xBB + i for i in range(26)})
ENCODE.update({chr(97 + i): 0xD5 + i for i in range(26)})
ENCODE.update({str(i): 0xA1 + i for i in range(10)})
DECODE = {value: key for key, value in ENCODE.items()}


def decode_name(raw):
    content = raw.split(b"\xff", 1)[0]
    if any(value not in DECODE for value in content):
        return None
    return "".join(DECODE[value] for value in content)


def encode_name(text, size, maximum=7):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("姓名不能为空")
    if len(text) > maximum:
        raise ValueError(f"姓名最多 {maximum} 个已支持字符，不会自动截断")
    if any(char not in ENCODE for char in text):
        raise ValueError(
            "当前姓名仅支持英文字母、数字、空格、连字符与英文单引号；中文编码待核验"
        )
    value = bytes(ENCODE[char] for char in text)
    if len(value) > size:
        raise ValueError("姓名超出存储容量")
    return value + b"\xff" * (size - len(value))
