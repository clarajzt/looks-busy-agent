"""Select text MIME sections without fetching attachment payloads (RFC 3501)."""
from __future__ import annotations

import base64
import quopri
import re


def decode_bytes(value: bytes, charset: str | None) -> str:
    try:
        return value.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return value.decode("utf-8", errors="replace")


def response_bytes(payload: list) -> bytes:
    pieces = []
    for item in payload:
        if isinstance(item, tuple):
            header, literal = item
            pieces.extend([header, b"\r\n", literal])
        elif isinstance(item, bytes):
            pieces.append(item)
    return b"".join(pieces)


def parse_structure(payload: list) -> list:
    raw = response_bytes(payload)
    match = re.search(rb"\bBODYSTRUCTURE\s+", raw, re.I)
    if not match:
        raise ValueError("missing BODYSTRUCTURE")
    position = match.end()

    def parse(depth=0):
        nonlocal position
        if depth > 50:
            raise ValueError("MIME nesting too deep")
        while position < len(raw) and raw[position:position+1].isspace():
            position += 1
        if position >= len(raw):
            raise ValueError("truncated BODYSTRUCTURE")
        token = raw[position:position+1]
        position += 1
        if token == b"(":
            result = []
            while True:
                while position < len(raw) and raw[position:position+1].isspace():
                    position += 1
                if raw[position:position+1] == b")":
                    position += 1
                    return result
                result.append(parse(depth + 1))
        if token == b'"':
            value = bytearray()
            while position < len(raw):
                char = raw[position:position+1]
                position += 1
                if char == b'"':
                    return decode_bytes(bytes(value), "utf-8")
                if char == b"\\":
                    char = raw[position:position+1]
                    position += 1
                value.extend(char)
            raise ValueError("unterminated quoted string")
        if token == b"{":
            end = raw.index(b"}", position)
            size = int(raw[position:end].rstrip(b"+"))
            position = end + 1
            if raw[position:position+2] != b"\r\n":
                raise ValueError("invalid literal")
            position += 2
            value = raw[position:position+size]
            position += size
            if len(value) != size:
                raise ValueError("truncated literal")
            return decode_bytes(value, "utf-8")
        start = position - 1
        while position < len(raw) and raw[position:position+1] not in b" ()\r\n":
            position += 1
        atom = raw[start:position].decode("ascii")
        return None if atom.upper() == "NIL" else atom

    result = parse()
    if not isinstance(result, list):
        raise ValueError("invalid BODYSTRUCTURE")
    return result


def parameters(value) -> dict:
    if not isinstance(value, list):
        return {}
    return {str(value[i]).upper(): value[i+1] for i in range(0, len(value) - 1, 2)}


def select_text_parts(structure: list, prefix: str = "") -> tuple[list[tuple], bool]:
    if not structure:
        raise ValueError("empty MIME structure")
    multipart = isinstance(structure[0], list)
    if multipart:
        count = next((i for i, value in enumerate(structure) if not isinstance(value, list)), len(structure))
        if count == len(structure):
            raise ValueError("missing multipart subtype")
        params = parameters(structure[count+1] if len(structure) > count+1 else None)
        disposition = structure[count+2] if len(structure) > count+2 else None
    else:
        if len(structure) < 7:
            raise ValueError("incomplete MIME leaf")
        params = parameters(structure[2])
        kind = str(structure[0]).upper()
        # A message/rfc822 subtree is never an outer-message body.
        if kind == "MESSAGE":
            return [], True
        index = 9 if kind == "TEXT" else 8
        disposition = structure[index] if len(structure) > index else None
    disposition_params = parameters(disposition[1] if isinstance(disposition, list) and len(disposition) > 1 else None)
    if (isinstance(disposition, list) and str(disposition[0]).upper() == "ATTACHMENT") or any(
        key.startswith(("NAME", "FILENAME")) for key in list(params) + list(disposition_params)
    ):
        return [], True
    if multipart:
        selections = [select_text_parts(child, f"{prefix}.{i}".strip(".")) for i, child in enumerate(structure[:count], 1)]
        parts = [part for selected, _ in selections for part in selected]
        if str(structure[count]).upper() == "ALTERNATIVE" and parts:
            plain = [part for part in parts if part[1] == "PLAIN"]
            parts = (plain or parts)[:1]
        return parts, any(attached for _, attached in selections)
    if kind == "TEXT" and str(structure[1]).upper() in ("PLAIN", "HTML"):
        return [(prefix or "1", str(structure[1]).upper(), params.get("CHARSET"), str(structure[5]).upper())], False
    return [], True


def fetch_literal(conn, uid: bytes, section: str, limit: int) -> bytes:
    status, payload = conn.uid("fetch", uid, f"(BODY.PEEK[{section}]<0.{limit}>)")
    if status != "OK":
        raise ValueError("MIME section unavailable")
    for item in payload:
        if isinstance(item, tuple) and isinstance(item[1], bytes):
            return item[1]
    raise ValueError("MIME literal missing")


def transfer_decode(raw: bytes, encoding: str, charset: str | None) -> str:
    if encoding == "BASE64":
        compact = re.sub(rb"\s+", b"", raw)
        # A partial fetch can stop in the middle of a base64 group.
        raw = base64.b64decode(compact[:len(compact) // 4 * 4])
    elif encoding == "QUOTED-PRINTABLE":
        raw = quopri.decodestring(raw)
    return decode_bytes(raw, charset)
