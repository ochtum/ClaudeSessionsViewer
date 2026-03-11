#!/usr/bin/env python3
import functools
import json
import locale
import os
import re
import struct
import subprocess
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8767"))
MAX_LIST = 400
MAX_EVENTS = 4000
MAX_DESKTOP_SCAN_BYTES = 2 * 1024 * 1024
SEARCH_TEXT_LIMIT = 50000


def _unique_paths(paths):
    out = []
    seen = set()
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _path_exists_safe(path: Path) -> bool:
    try:
        return path.exists()
    except Exception:
        return False


def _decode_process_stdout(raw: bytes) -> str:
    if not raw:
        return ""
    encodings = ["utf-16le", "utf-8", locale.getpreferredencoding(False)] if b"\x00" in raw else ["utf-8", locale.getpreferredencoding(False), "utf-16le"]
    seen = set()
    for enc in encodings:
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            return raw.decode(enc).replace("\x00", "").strip()
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace").replace("\x00", "").strip()


def _run_command_capture(cmd, timeout=5):
    try:
        completed = subprocess.run(cmd, capture_output=True, check=False, timeout=timeout)
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return _decode_process_stdout(completed.stdout)


def _split_simple_list(raw: str):
    if not isinstance(raw, str):
        return []
    return [x.strip() for x in re.split(r"[;,\r\n]+", raw) if x.strip()]


def _wsl_unc_path(distro: str, posix_path: str):
    if not distro or not isinstance(posix_path, str) or not posix_path.startswith("/"):
        return None
    suffix = posix_path.strip("/").replace("/", "\\")
    base = rf"\\wsl.localhost\{distro}"
    return Path(base if not suffix else f"{base}\\{suffix}")


@functools.lru_cache(maxsize=1)
def _get_wsl_distros_on_windows():
    if os.name != "nt":
        return []

    override = os.getenv("CLAUDE_WSL_DISTROS")
    if override:
        return _split_simple_list(override)

    raw = _run_command_capture(["wsl.exe", "-l", "-q"], timeout=6)
    if not raw:
        return []
    return _split_simple_list(raw)


def _get_wsl_home_on_windows(distro: str) -> str:
    if os.name != "nt" or not distro:
        return ""
    raw = _run_command_capture(["wsl.exe", "-d", distro, "sh", "-lc", "printf '%s' \"$HOME\""], timeout=8)
    return raw if raw.startswith("/") else ""


@functools.lru_cache(maxsize=1)
def _get_wsl_cli_roots_on_windows():
    distros = _get_wsl_distros_on_windows()
    if not distros:
        return []

    candidates = []
    for distro in distros:
        actual_home = _get_wsl_home_on_windows(distro)
        actual_home_root = _wsl_unc_path(distro, actual_home) if actual_home else None
        if actual_home_root:
            candidates.append(actual_home_root / ".claude" / "projects")

        home_root = _wsl_unc_path(distro, "/home")
        if home_root and _path_exists_safe(home_root):
            try:
                for d in home_root.iterdir():
                    try:
                        if d.is_dir():
                            candidates.append(d / ".claude" / "projects")
                    except Exception:
                        continue
            except Exception:
                pass
        root_home = _wsl_unc_path(distro, "/root")
        if root_home:
            candidates.append(root_home / ".claude" / "projects")

    candidates = _unique_paths(candidates)
    existing = [p for p in candidates if _path_exists_safe(p)]
    return existing if existing else candidates


def _iso_from_ts(ts):
    if isinstance(ts, (int, float)):
        try:
            if ts > 1_000_000_000_000:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts).isoformat()
        except Exception:
            return ""
    if isinstance(ts, str):
        s = ts.strip()
        if not s:
            return ""
        if re.fullmatch(r"\d{10,16}", s):
            try:
                n = int(s)
                if n > 1_000_000_000_000:
                    n = n / 1000.0
                return datetime.fromtimestamp(n).isoformat()
            except Exception:
                return ""
        return s
    return ""


def _resolve_roots_from_env():
    raw = os.getenv("CLAUDE_SESSIONS_DIR") or os.getenv("SESSIONS_DIR")
    if not raw:
        return None
    parts = [x.strip() for x in raw.split(os.pathsep) if x.strip()]
    return _unique_paths([Path(x).expanduser() for x in parts])


@functools.lru_cache(maxsize=1)
def get_claude_cli_roots():
    env_roots = _resolve_roots_from_env()
    if env_roots is not None:
        return env_roots

    candidates = []
    home = Path.home()
    userprofile = os.getenv("USERPROFILE")
    win_home = os.getenv("WIN_HOME")

    candidates.append(home / ".claude" / "projects")

    if userprofile:
        candidates.append(Path(userprofile) / ".claude" / "projects")
    if win_home:
        candidates.append(Path(win_home) / ".claude" / "projects")

    users_root = Path("/mnt/c/Users")
    if _path_exists_safe(users_root):
        try:
            dirs = list(users_root.iterdir())
        except Exception:
            dirs = []
        for d in dirs:
            try:
                if d.is_dir():
                    candidates.append(d / ".claude" / "projects")
            except Exception:
                continue

    candidates.extend(_get_wsl_cli_roots_on_windows())

    candidates = _unique_paths(candidates)
    existing = [p for p in candidates if _path_exists_safe(p)]
    return existing if existing else candidates


@functools.lru_cache(maxsize=1)
def get_claude_desktop_roots():
    candidates = []
    appdata = os.getenv("APPDATA")
    userprofile = os.getenv("USERPROFILE")
    win_home = os.getenv("WIN_HOME")

    if appdata:
        candidates.append(Path(appdata) / "Claude" / "IndexedDB")
    if userprofile:
        candidates.append(Path(userprofile) / "AppData" / "Roaming" / "Claude" / "IndexedDB")
    if win_home:
        candidates.append(Path(win_home) / "AppData" / "Roaming" / "Claude" / "IndexedDB")

    users_root = Path("/mnt/c/Users")
    if _path_exists_safe(users_root):
        try:
            dirs = list(users_root.iterdir())
        except Exception:
            dirs = []
        for d in dirs:
            try:
                if d.is_dir():
                    candidates.append(d / "AppData" / "Roaming" / "Claude" / "IndexedDB")
            except Exception:
                continue

    candidates = _unique_paths(candidates)
    existing = [p for p in candidates if _path_exists_safe(p)]
    return existing if existing else candidates


def get_roots():
    return {
        "claude_cli": get_claude_cli_roots(),
        "claude_desktop": get_claude_desktop_roots(),
    }


def _iter_cli_jsonl_files(root: Path):
    if not _path_exists_safe(root):
        return []
    try:
        return [p for p in root.rglob("*.jsonl") if p.is_file()]
    except Exception:
        return []


def _iter_desktop_leveldb_files(root: Path):
    if not _path_exists_safe(root):
        return []
    out = []
    patterns = ("*.ldb", "*.log", "MANIFEST-*")
    for pat in patterns:
        try:
            out.extend([p for p in root.rglob(pat) if p.is_file()])
        except Exception:
            continue
    return out


def iter_all_session_files():
    roots = get_roots()
    files = []
    for root in roots["claude_cli"]:
        files.extend([("claude_cli", p, root) for p in _iter_cli_jsonl_files(root)])
    for root in roots["claude_desktop"]:
        files.extend([("claude_desktop", p, root) for p in _iter_desktop_leveldb_files(root)])
    files.sort(key=lambda x: x[1].stat().st_mtime if x[1].exists() else 0, reverse=True)
    return files


def _extract_text_recursive(obj):
    texts = []
    if isinstance(obj, str):
        s = obj.strip()
        if s:
            texts.append(s)
    elif isinstance(obj, list):
        for item in obj:
            texts.extend(_extract_text_recursive(item))
    elif isinstance(obj, dict):
        text_keys = ("text", "content", "message", "prompt", "output", "input", "value", "body")
        for k in text_keys:
            if k in obj:
                texts.extend(_extract_text_recursive(obj.get(k)))
        skip_keys = {
            "type",
            "id",
            "uuid",
            "role",
            "sender",
            "author",
            "version",
            "updatedAt",
            "createdAt",
            "timestamp",
            "time",
            "ts",
        }
        for k, v in obj.items():
            if k in text_keys or k in skip_keys:
                continue
            texts.extend(_extract_text_recursive(v))
    return texts


def _guess_role(obj):
    if not isinstance(obj, dict):
        return "system"
    msg = obj.get("message")
    if isinstance(msg, dict):
        msg_role = msg.get("role")
        if isinstance(msg_role, str):
            low = msg_role.lower()
            if low in ("user", "human"):
                return "user"
            if low in ("assistant", "claude", "ai"):
                return "assistant"
            if low in ("developer", "dev"):
                return "developer"
            if low == "system":
                return "system"
    for key in ("role", "sender", "author"):
        val = obj.get(key)
        if isinstance(val, str):
            low = val.lower()
            if low in ("user", "human"):
                return "user"
            if low in ("assistant", "claude", "ai"):
                return "assistant"
            if low in ("developer", "dev"):
                return "developer"
            if low == "system":
                return "system"
    typ = obj.get("type")
    if isinstance(typ, str):
        low = typ.lower()
        if low in ("user", "human_message", "human"):
            return "user"
        if low in ("assistant", "assistant_message"):
            return "assistant"
        if low in ("system", "system_message"):
            return "system"
    return "system"


def _extract_claude_message_text(message_obj):
    if isinstance(message_obj, str):
        return message_obj.strip()
    if not isinstance(message_obj, dict):
        return ""

    content = message_obj.get("content")
    if isinstance(content, str):
        return content.strip()

    chunks = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                if isinstance(item, str) and item.strip():
                    chunks.append(item.strip())
                continue
            typ = item.get("type")
            if typ == "text":
                t = item.get("text")
                if isinstance(t, str) and t.strip():
                    chunks.append(t.strip())
            elif typ == "thinking":
                # Claude UIで通常表示されないthinkingは詳細表示テキストから除外する
                continue
            elif typ == "tool_use":
                name = item.get("name", "")
                tool_input = item.get("input")
                if isinstance(tool_input, dict):
                    arg = json.dumps(tool_input, ensure_ascii=False, indent=2)
                else:
                    arg = str(tool_input or "")
                chunks.append(f"[tool_use] {name}\n{arg}".strip())
            elif typ == "tool_result":
                t = "\n".join(_extract_text_recursive(item.get("content")))
                if t.strip():
                    chunks.append(f"[tool_result] {t.strip()}")
            else:
                t = "\n".join(_extract_text_recursive(item))
                if t.strip():
                    chunks.append(t.strip())
    if chunks:
        return "\n".join(chunks).strip()
    return "\n".join(_extract_text_recursive(message_obj)).strip()


def _is_tool_result_message(message_obj):
    if not isinstance(message_obj, dict):
        return False
    content = message_obj.get("content")
    if not isinstance(content, list):
        return False
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_result":
            return True
    return False


def _is_skills_instruction_text(text: str) -> bool:
    if not isinstance(text, str):
        return False
    return text.lstrip().lower().startswith("base directory for this skill:")


def _extract_claude_progress_text(obj):
    data = obj.get("data")
    if not isinstance(data, dict):
        return ""
    typ = data.get("type")
    if typ == "mcp_progress":
        return (
            f"mcp_progress status={data.get('status','')} "
            f"server={data.get('serverName','')} tool={data.get('toolName','')} "
            f"elapsed={data.get('elapsedTimeMs','')}"
        ).strip()
    if typ == "hook_progress":
        return (
            f"hook_progress event={data.get('hookEvent','')} "
            f"name={data.get('hookName','')} command={data.get('command','')}"
        ).strip()
    return json.dumps(data, ensure_ascii=False)


def _extract_ts_from_obj(obj):
    if not isinstance(obj, dict):
        return ""
    for key in ("timestamp", "time", "created_at", "createdAt", "ts"):
        if key in obj:
            parsed = _iso_from_ts(obj.get(key))
            if parsed:
                return parsed
    return ""


def _safe_rel(path: Path, root: Path):
    try:
        return str(path.relative_to(root))
    except Exception:
        return str(path)


def _extract_wsl_distro_from_root(root: Path) -> str:
    if root is None:
        return ""
    m = re.match(r"^\\\\wsl(?:\.localhost)?\\([^\\]+)(?:\\|$)", str(root), re.IGNORECASE)
    return m.group(1) if m else ""


def _to_windows_path_display(path_str: str, wsl_distro: str = "") -> str:
    if not isinstance(path_str, str):
        return ""
    s = path_str.strip()
    if not s:
        return ""
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", s)
    if m:
        drive = m.group(1).upper()
        rest = m.group(2).replace("/", "\\")
        return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
    if s.startswith("/"):
        if wsl_distro:
            rest = s.lstrip("/").replace("/", "\\")
            base = f"\\\\wsl.localhost\\{wsl_distro}"
            return f"{base}\\{rest}" if rest else base
        return s
    converted = s.replace("/", "\\")
    # Some records can carry slug-like values such as "C:\\-foo-bar-baz".
    # Treat this as a project slug and normalize it into "C:\\foo\\bar\\baz".
    m2 = re.match(r"^([a-zA-Z]:)\\-([^\\]+)$", converted)
    if m2:
        drive = m2.group(1).upper()
        tail = "\\".join([p for p in m2.group(2).split("-") if p])
        return f"{drive}\\{tail}" if tail else f"{drive}\\"
    return converted


def _decode_project_slug_to_windows_path(project_slug: str) -> str:
    if not isinstance(project_slug, str):
        return ""
    s = project_slug.strip()
    if not s:
        return ""
    if "/" in s or "\\" in s or "-" not in s:
        return s

    parts = [p for p in s.lstrip("-").split("-") if p]
    if not parts:
        return s

    if len(parts) >= 3 and parts[0].lower() == "mnt" and len(parts[1]) == 1 and parts[1].isalpha():
        drive = parts[1].upper()
        tail = "\\".join(parts[2:])
        return f"{drive}:\\{tail}" if tail else f"{drive}:\\"

    if len(parts) >= 2 and len(parts[0]) == 1 and parts[0].isalpha():
        drive = parts[0].upper()
        tail = "\\".join(parts[1:])
        return f"{drive}:\\{tail}" if tail else f"{drive}:\\"

    return "\\".join(parts)


def _project_display_label(raw_project: str, cwd: str, root: Path = None) -> str:
    wsl_distro = _extract_wsl_distro_from_root(root)
    if isinstance(cwd, str) and cwd.strip():
        return _to_windows_path_display(cwd, wsl_distro=wsl_distro)
    label = _decode_project_slug_to_windows_path(raw_project)
    if wsl_distro and label and not re.match(r"^(?:[a-zA-Z]:\\|\\\\)", label):
        return f"\\\\wsl.localhost\\{wsl_distro}\\{label.lstrip('\\')}"
    return label


def _is_probably_textual_json_line(line: str):
    s = line.strip()
    return s.startswith("{") and s.endswith("}")


def _extract_json_candidates_balanced(text: str, limit=200):
    out = []
    n = len(text)
    i = 0
    while i < n and len(out) < limit:
        if text[i] != "{":
            i += 1
            continue
        start = i
        depth = 0
        in_str = False
        esc = False
        j = i
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        chunk = text[start : j + 1]
                        if 24 <= len(chunk) <= 200_000:
                            out.append(chunk)
                        break
            j += 1
        i = j + 1 if j > i else i + 1
    return out


def _extract_json_objects_from_text(text: str, limit=120):
    objs = []
    seen = set()
    for chunk in _extract_json_candidates_balanced(text, limit=limit * 6):
        if '"text"' not in chunk and '"content"' not in chunk and '"prompt"' not in chunk and '"message"' not in chunk:
            continue
        key = chunk[:400]
        if key in seen:
            continue
        seen.add(key)
        try:
            obj = json.loads(chunk)
        except Exception:
            continue
        if isinstance(obj, dict):
            objs.append(obj)
            if len(objs) >= limit:
                break
    return objs


def _extract_json_objects_from_bytes(raw: bytes, limit=120):
    texts = [
        raw.decode("utf-8", errors="ignore"),
        raw.decode("utf-16le", errors="ignore"),
    ]
    out = []
    seen = set()
    for text in texts:
        if not text:
            continue
        objs = _extract_json_objects_from_text(text, limit=limit)
        for obj in objs:
            sig = json.dumps(obj, ensure_ascii=False)[:400]
            if sig in seen:
                continue
            seen.add(sig)
            out.append(obj)
            if len(out) >= limit:
                return out
    return out


def _extract_readable_snippets(raw: bytes, limit=12):
    snippets = []
    texts = [
        raw.decode("utf-8", errors="ignore"),
        raw.decode("utf-16le", errors="ignore"),
    ]
    seen = set()
    for text in texts:
        if not text:
            continue
        for m in re.finditer(r"[ -~\u3040-\u30FF\u4E00-\u9FFF]{24,300}", text):
            s = m.group(0).strip()
            if len(s) < 24:
                continue
            if "IndexedDB" in s or "LEVELDB" in s:
                continue
            key = s[:160]
            if key in seen:
                continue
            seen.add(key)
            snippets.append(s)
            if len(snippets) >= limit:
                return snippets
    return snippets


# ---------------------------------------------------------------------------
# LevelDB log file parser for Chromium IndexedDB (Claude Desktop)
# ---------------------------------------------------------------------------

def _ldb_read_varint(data: bytes, pos: int):
    """Read a protobuf-style varint. Returns (value, new_pos)."""
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _ldb_parse_log(raw: bytes):
    """Parse a LevelDB write-ahead log file.

    Returns a list of (key, value) tuples in write order.
    LevelDB log format: 32 KB blocks, each containing records.
    Record header: CRC32(4) + length(2) + type(1) + data(length).
    Record data is a WriteBatch: seq(8) + count(4) + entries.
    """
    BLOCK_SIZE = 32768
    HEADER_SIZE = 7
    records = []
    pos = 0
    fragment = b""

    while pos < len(raw):
        block_offset = pos % BLOCK_SIZE
        if BLOCK_SIZE - block_offset < HEADER_SIZE:
            pos += BLOCK_SIZE - block_offset
            continue
        if pos + HEADER_SIZE > len(raw):
            break
        length = struct.unpack_from("<H", raw, pos + 4)[0]
        rtype = raw[pos + 6]
        pos += HEADER_SIZE
        if length == 0 and rtype == 0:
            # Zero padding — skip to next block boundary
            pos = ((pos - 1) // BLOCK_SIZE + 1) * BLOCK_SIZE
            continue
        if pos + length > len(raw):
            break
        data = raw[pos : pos + length]
        pos += length
        if rtype == 1:  # kFullType
            records.append(data)
        elif rtype == 2:  # kFirstType
            fragment = data
        elif rtype == 3:  # kMiddleType
            fragment += data
        elif rtype == 4:  # kLastType
            records.append(fragment + data)
            fragment = b""

    entries = []
    for record in records:
        if len(record) < 12:
            continue
        count = struct.unpack_from("<I", record, 8)[0]
        rpos = 12
        for _ in range(count):
            if rpos >= len(record):
                break
            vtype = record[rpos]
            rpos += 1
            key_len, rpos = _ldb_read_varint(record, rpos)
            key = record[rpos : rpos + key_len]
            rpos += key_len
            if vtype == 1:  # kTypeValue
                val_len, rpos = _ldb_read_varint(record, rpos)
                val = record[rpos : rpos + val_len]
                rpos += val_len
                entries.append((key, val))
    return entries


def _ldb_decode_idb_string_key(key_bytes: bytes):
    """Try to extract the IDB user-space string key from a LevelDB key.

    Chromium IndexedDB data keys have a binary prefix encoding
    (database_id, object_store_id, index_id) followed by the IDB key.
    For string IDB keys Claude Desktop uses, the string is encoded in
    UTF-16BE starting at offset 6 of the raw LevelDB key.
    Returns the decoded string, or None if not recognizable.
    """
    if len(key_bytes) < 8:
        return None
    # Try UTF-16BE decoding from offset 6
    rest = key_bytes[6:]
    if len(rest) < 4:
        return None
    try:
        s = rest.decode("utf-16-be", errors="strict")
        # Validate: must be printable and look like a key string
        if len(s) >= 3 and all(c.isprintable() or c in (" ", "\n", "\t") for c in s):
            ascii_ratio = sum(1 for c in s if ord(c) < 128) / len(s)
            if ascii_ratio >= 0.5:
                return s
    except Exception:
        pass
    return None


def _ldb_extract_json_from_value(val: bytes):
    """Extract the JSON object embedded in a Blink-serialized IndexedDB value.

    Claude Desktop stores JavaScript objects as Blink SerializedScriptValue.
    The actual data is UTF-16LE encoded JSON preceded by a binary header.
    We locate the first occurrence of '{' (0x7B 0x00 in UTF-16LE) and
    decode from there.
    Returns a parsed dict, or None.
    """
    idx = val.find(b"\x7b\x00")  # '{' in UTF-16LE
    if idx < 0:
        return None
    text = val[idx:].decode("utf-16-le", errors="ignore")
    if not text.startswith("{"):
        return None
    # Walk to find the balanced closing brace
    depth = 0
    in_str = False
    esc = False
    end = -1
    for i, c in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
    if end < 0:
        return None
    try:
        return json.loads(text[:end])
    except Exception:
        return None


def _ldb_extract_tiptap_texts(node):
    """Recursively extract plain text strings from a TipTap/ProseMirror doc node."""
    if isinstance(node, dict):
        if node.get("type") == "text":
            t = node.get("text", "")
            return [t] if t else []
        result = []
        for child in node.get("content", []):
            result.extend(_ldb_extract_tiptap_texts(child))
        return result
    if isinstance(node, list):
        result = []
        for item in node:
            result.extend(_ldb_extract_tiptap_texts(item))
        return result
    return []


def _ldb_parse_desktop_entries(path: Path):
    """Parse a LevelDB .log file and return a list of decoded chat-draft dicts.

    Each dict has keys: idb_key, text, updated_at (ISO string), attachments.
    Only data entries with meaningful IDB string keys are returned.
    Duplicate keys are deduplicated (latest write wins).
    """
    try:
        with path.open("rb") as f:
            raw = f.read(min(MAX_DESKTOP_SCAN_BYTES, path.stat().st_size))
    except Exception:
        return []

    raw_entries = _ldb_parse_log(raw)
    if not raw_entries:
        return []

    # Deduplicate: later writes override earlier ones
    latest: dict = {}
    for k, v in raw_entries:
        latest[k] = v

    results = []
    for k, v in latest.items():
        idb_key = _ldb_decode_idb_string_key(k)
        if not idb_key:
            continue
        obj = _ldb_extract_json_from_value(v)
        if not obj:
            continue

        # Extract chat content from the JSON structure
        updated_at = ""
        raw_ts = obj.get("updatedAt")
        if raw_ts:
            updated_at = _iso_from_ts(raw_ts)

        state = obj.get("state", obj)
        editor_state = state.get("tipTapEditorState") or state.get("editorState")
        texts = _ldb_extract_tiptap_texts(editor_state) if editor_state else []
        if not texts:
            texts = _extract_text_recursive(obj)

        draft_text = "".join(texts).strip()
        if not draft_text:
            continue

        # Attachment info
        attachments = state.get("attachments", []) if isinstance(state, dict) else []
        files = state.get("files", []) if isinstance(state, dict) else []
        attach_count = len(attachments) + len(files)

        results.append(
            {
                "idb_key": idb_key,
                "text": draft_text,
                "updated_at": updated_at,
                "attach_count": attach_count,
            }
        )

    # Sort by updated_at ascending
    results.sort(key=lambda x: x["updated_at"])
    return results


def summarize_cli_session(path: Path, root: Path):
    summary = {
        "id": path.stem,
        "path": str(path),
        "relative_path": _safe_rel(path, root),
        "source": "Claude Code CLI",
        "source_type": "claude_cli",
        "project": "",
        "mtime": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        "started_at": "",
        "cwd": "",
        "model": "",
        "first_user_text": "",
        "search_text": "",
    }

    rel = summary["relative_path"]
    if "/" in rel:
        summary["project"] = rel.split("/", 1)[0]
    elif "\\" in rel:
        summary["project"] = rel.split("\\", 1)[0]

    search_chunks = []
    search_limit = SEARCH_TEXT_LIMIT
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not _is_probably_textual_json_line(line):
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not summary["started_at"]:
                    summary["started_at"] = _extract_ts_from_obj(obj)
                if not summary["model"]:
                    for k in ("model", "model_name", "modelName"):
                        v = obj.get(k) if isinstance(obj, dict) else None
                        if isinstance(v, str) and v:
                            summary["model"] = v
                            break
                if not summary["cwd"]:
                    v = obj.get("cwd") if isinstance(obj, dict) else None
                    if isinstance(v, str):
                        summary["cwd"] = v
                role = _guess_role(obj)
                texts = _extract_text_recursive(obj)
                if texts:
                    text = " ".join(texts).strip()
                    if role == "user" and not summary["first_user_text"]:
                        summary["first_user_text"] = text.replace("\n", " ")[:180]
                    if len(" ".join(search_chunks)) < search_limit:
                        search_chunks.append(text.replace("\n", " ")[:320])
                if summary["first_user_text"] and len(" ".join(search_chunks)) >= search_limit:
                    break
    except Exception:
        pass

    summary["project"] = _project_display_label(summary["project"], summary["cwd"], root)
    summary["search_text"] = " ".join(search_chunks)
    return summary


def summarize_desktop_blob(path: Path, root: Path):
    summary = {
        "id": path.name,
        "path": str(path),
        "relative_path": _safe_rel(path, root),
        "source": "Claude Desktop (IndexedDB/LevelDB)",
        "source_type": "claude_desktop",
        "project": "(desktop)",
        "mtime": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        "started_at": "",
        "cwd": "",
        "model": "",
        "first_user_text": "",
        "search_text": "",
    }

    # Try the proper LevelDB log parser first (for *.log files)
    if path.suffix == ".log":
        entries = _ldb_parse_desktop_entries(path)
        if entries:
            texts = []
            for e in entries:
                if not summary["started_at"] and e["updated_at"]:
                    summary["started_at"] = e["updated_at"]
                t = e["text"].replace("\n", " ")
                if not summary["first_user_text"]:
                    summary["first_user_text"] = t[:180]
                texts.append(t[:320])
            summary["search_text"] = " ".join(texts)[:SEARCH_TEXT_LIMIT]
            return summary

    # Fallback: raw binary scan
    try:
        with path.open("rb") as f:
            raw = f.read(min(MAX_DESKTOP_SCAN_BYTES, max(256 * 1024, path.stat().st_size)))
    except Exception:
        return summary

    objs = _extract_json_objects_from_bytes(raw, limit=40)
    if objs:
        texts = []
        for obj in objs:
            if not summary["started_at"]:
                summary["started_at"] = _extract_ts_from_obj(obj)
            role = _guess_role(obj)
            parts = _extract_text_recursive(obj)
            if parts:
                merged = " ".join(parts).strip()
                if role == "user" and not summary["first_user_text"]:
                    summary["first_user_text"] = merged.replace("\n", " ")[:180]
                texts.append(merged.replace("\n", " ")[:320])
        summary["search_text"] = " ".join(texts)[:SEARCH_TEXT_LIMIT]
        if not summary["first_user_text"] and texts:
            summary["first_user_text"] = texts[0][:180]
    else:
        snippets = _extract_readable_snippets(raw, limit=10)
        summary["search_text"] = " ".join(snippets)
        if snippets and not summary["first_user_text"]:
            summary["first_user_text"] = snippets[0][:180]
    return summary


def summarize_session(source_type: str, path: Path, root: Path):
    if source_type == "claude_cli":
        return summarize_cli_session(path, root)
    return summarize_desktop_blob(path, root)


def load_cli_events(path: Path):
    events = []
    raw_count = 0
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            raw_count += 1
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ts = _extract_ts_from_obj(obj)
            typ = obj.get("type", "")
            role = _guess_role(obj)
            kind = "event"
            text = ""

            if typ == "user":
                if _is_tool_result_message(obj.get("message")):
                    kind = "tool_result"
                    role = "tool"
                else:
                    kind = "message"
                    role = "user"
                text = _extract_claude_message_text(obj.get("message"))
            elif typ == "assistant":
                kind = "message"
                role = "assistant"
                text = _extract_claude_message_text(obj.get("message"))
            elif typ == "queue-operation":
                kind = "queue"
                role = "system"
                op = obj.get("operation", "")
                content = obj.get("content", "")
                text = f"{op}\n{content}".strip()
            elif typ == "progress":
                kind = "progress"
                role = "system"
                text = _extract_claude_progress_text(obj)
            elif typ == "system":
                kind = "system"
                role = "system"
                text = json.dumps(obj, ensure_ascii=False)
            else:
                text = _extract_claude_message_text(obj.get("message"))
                if not text:
                    text = "\n".join(_extract_text_recursive(obj)).strip()

            if not text:
                text = json.dumps(obj, ensure_ascii=False)[:1000]
            labels = []
            if kind == "message" and role == "user" and _is_skills_instruction_text(text):
                labels.append("SKILLS")
            ev = {"timestamp": ts, "kind": kind, "role": role, "text": text}
            if labels:
                ev["labels"] = labels
            events.append(ev)
            if len(events) >= MAX_EVENTS:
                break
    return {"events": events, "raw_line_count": raw_count}


def load_desktop_events(path: Path):
    events = []

    # Try proper LevelDB log parser first (for *.log files)
    if path.suffix == ".log":
        entries = _ldb_parse_desktop_entries(path)
        if entries:
            notice = (
                "Claude Desktop の IndexedDB(LevelDB) から chat-draft エントリを解析しました。"
                " これらは送信前の下書きメッセージです。送信済み会話は claude.ai サーバー側に保存されています。"
            )
            events.append({"timestamp": "", "kind": "notice", "role": "system", "text": notice})
            for e in entries:
                key = e["idb_key"]
                # Extract conversation ID from key (e.g. "store:chat-draft:conv-id")
                parts = key.split(":")
                conv_label = parts[-1] if len(parts) >= 3 else key
                attach_note = f"  [添付 {e['attach_count']} 件]" if e["attach_count"] else ""
                text = f"[{conv_label}]\n{e['text']}{attach_note}"
                events.append(
                    {
                        "timestamp": e["updated_at"],
                        "kind": "message",
                        "role": "user",
                        "text": text,
                    }
                )
            return {"events": events[:MAX_EVENTS], "raw_line_count": len(entries)}

    # Fallback: raw binary scan
    with path.open("rb") as f:
        raw = f.read(min(MAX_DESKTOP_SCAN_BYTES, max(256 * 1024, path.stat().st_size)))

    objs = _extract_json_objects_from_bytes(raw, limit=MAX_EVENTS)
    if objs:
        for obj in objs:
            text = "\n".join(_extract_text_recursive(obj)).strip()
            if not text:
                continue
            events.append(
                {
                    "timestamp": _extract_ts_from_obj(obj),
                    "kind": "snippet",
                    "role": _guess_role(obj),
                    "text": text[:4000],
                }
            )
    else:
        for s in _extract_readable_snippets(raw, limit=800):
            events.append({"timestamp": "", "kind": "snippet", "role": "system", "text": s})

    notice = (
        "Claude Desktop の IndexedDB(LevelDB) はバイナリ形式のため、ここでは文字列/JSONスニペット抽出で表示しています。"
        " 完全な履歴復元ではありません。"
    )
    events.insert(0, {"timestamp": "", "kind": "notice", "role": "system", "text": notice})
    return {"events": events[:MAX_EVENTS], "raw_line_count": len(events)}


def load_session_events(source_type: str, path: Path):
    if source_type == "claude_cli":
        return load_cli_events(path)
    return load_desktop_events(path)


HTML_PAGE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Claude Sessions Viewer</title>
<style>
:root {
  --bg: #f2f6fb;
  --panel: #ffffff;
  --line: #ccd8e4;
  --text: #18232f;
  --muted: #57697c;
  --accent: #0d6d77;
  --user: #1b5fd6;
  --assistant: #0f7c4f;
  --dev: #8a5a00;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  font-family: "Segoe UI", "Yu Gothic UI", sans-serif;
  background: radial-gradient(circle at top right, #e6f4ff 0%, var(--bg) 45%);
  color: var(--text);
  overflow: hidden;
}
header {
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
  background: rgba(255,255,255,0.9);
  backdrop-filter: blur(4px);
}
header h1 { margin: 0; font-size: 18px; }
header small { color: var(--muted); display: block; margin-top: 4px; }
.container {
  display: grid;
  grid-template-columns: 390px 1fr;
  height: calc(100vh - 80px);
  overflow: hidden;
}
.left {
  border-right: 1px solid var(--line);
  background: #f9fcff;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.toolbar {
  padding: 10px;
  border-bottom: 1px solid var(--line);
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}
input, select, button {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
}
#project_q, #q { flex: 1 1 220px; }
#date_from, #date_to { flex: 1 1 185px; }
#mode, #source_filter { flex: 0 0 auto; }
button {
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  white-space: nowrap;
}
#sessions {
  overflow: auto;
  flex: 1;
}
.session-item {
  padding: 10px 12px;
  border-bottom: 1px solid #e7eef6;
  cursor: pointer;
}
.session-item:hover { background: #eef7ff; }
.session-item.active { background: #dff0ff; }
.session-path {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-tags {
  margin-top: 4px;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  white-space: normal;
  overflow: visible;
}
.session-project {
  color: #0b5f3d;
  font-size: 11px;
  font-weight: 600;
  background: #e8f7ef;
  border: 1px solid #bfe8cf;
  border-radius: 6px;
  padding: 1px 6px;
  display: inline-block;
  max-width: 100%;
}
.session-source {
  color: #5f3f0b;
  font-size: 11px;
  font-weight: 600;
  background: #fff3de;
  border: 1px solid #f0d3a1;
  border-radius: 6px;
  padding: 1px 6px;
  display: inline-block;
  max-width: 100%;
  margin-left: 6px;
}
.session-source.cli {
  color: #0a3f8a;
  background: #e7efff;
  border-color: #b9cdf8;
}
.session-source.desktop {
  color: #6b4300;
  background: #fff3de;
  border-color: #f0d3a1;
}
.session-time {
  color: #0b4a52;
  font-size: 11px;
  font-weight: 600;
  background: #dff5f8;
  border: 1px solid #b8dee3;
  border-radius: 6px;
  padding: 1px 6px;
  display: inline-block;
  max-width: 100%;
  margin-left: 6px;
  font-variant-numeric: tabular-nums;
}
.session-preview {
  margin-top: 4px;
  font-size: 12px;
  color: #34414f;
}
.right {
  background: var(--panel);
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.meta {
  padding: 12px;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
  color: var(--muted);
}
.meta code.path-code {
  color: #0b4a52;
  background: #e5f4f6;
  border: 1px solid #b8dee3;
  padding: 2px 6px;
  border-radius: 6px;
  font-weight: 700;
}
.meta code.project-code {
  color: #0b5f3d;
  background: #e8f7ef;
  border: 1px solid #bfe8cf;
  padding: 2px 6px;
  border-radius: 6px;
  font-weight: 700;
}
.meta code.source-code {
  border: 1px solid #d4dce5;
  padding: 2px 6px;
  border-radius: 6px;
  font-weight: 700;
}
.meta code.source-code.cli {
  color: #0a3f8a;
  background: #e7efff;
  border-color: #b9cdf8;
}
.meta code.source-code.desktop {
  color: #6b4300;
  background: #fff3de;
  border-color: #f0d3a1;
}
.detail-toolbar {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  align-items: center;
  background: #f8fbff;
}
.detail-toolbar label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #324255;
  user-select: none;
}
.detail-toolbar button {
  height: 28px;
  border: 1px solid #c5d3e6;
  background: #f1f6ff;
  color: #1e3a5f;
  border-radius: 6px;
  padding: 0 10px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.detail-toolbar #copy_resume_cmd {
  background: #0f766e;
  border-color: #0f766e;
  color: #ffffff;
}
.detail-toolbar #copy_resume_cmd:disabled {
  background: #94a3b8;
  border-color: #94a3b8;
  color: #f8fafc;
  cursor: not-allowed;
}
.detail-toolbar button:disabled {
  background: #94a3b8;
  border-color: #94a3b8;
  color: #f8fafc;
  opacity: 0.6;
  cursor: not-allowed;
}
#events {
  padding: 14px;
  overflow: auto;
  flex: 1;
}
.ev {
  border: 1px solid var(--line);
  border-left-width: 5px;
  border-radius: 10px;
  padding: 10px;
  margin-bottom: 10px;
  background: #fff;
}
.ev.user { border-left-color: var(--user); background: #eaf3ff; }
.ev.assistant { border-left-color: var(--assistant); background: #ecf9f1; }
.ev.developer { border-left-color: var(--dev); }
.ev.system { border-left-color: #6b7280; background: #f6f7f9; }
.ev.tool { border-left-color: #0f766e; background: #e8fbf8; }
.ev.kind-message { box-shadow: inset 0 0 0 1px rgba(20, 90, 160, 0.08); }
.ev.kind-queue { border-left-color: #a855f7; background: #f5efff; }
.ev.kind-progress { border-left-color: #f59e0b; background: #fff7e6; }
.ev.kind-notice { border-left-color: #0ea5e9; background: #eaf7ff; }
.ev.kind-system { border-left-color: #64748b; background: #f1f5f9; }
.ev.kind-tool-result { border-left-color: #0f766e; background: #dff7f2; box-shadow: inset 0 0 0 1px rgba(15, 118, 110, 0.15); }
.ev-head {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 8px;
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.ev-badges {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.ev-badge {
  display: inline-block;
  border-radius: 999px;
  padding: 1px 8px;
  border: 1px solid #d9c289;
  background: #fff7e6;
  color: #8a5d00;
  font-weight: 700;
  letter-spacing: 0.01em;
}
.ev-role {
  display: inline-block;
  border-radius: 999px;
  padding: 1px 8px;
  border: 1px solid var(--line);
  font-weight: 700;
}
.ev-role.user {
  color: #0a3f8a;
  background: #e7efff;
  border-color: #b9cdf8;
}
.ev-role.assistant {
  color: #0d6a40;
  background: #e5f7ed;
  border-color: #b7e8cb;
}
.ev-role.system {
  color: #4b5563;
  background: #f1f5f9;
  border-color: #d4dce5;
}
.ev-role.tool {
  color: #0f5f58;
  background: #dcf5ef;
  border-color: #97ddd0;
}
pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  font-size: 13px;
  line-height: 1.55;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  background: rgba(255, 255, 255, 0.65);
  border: 1px solid rgba(148, 163, 184, 0.32);
  border-radius: 8px;
  padding: 10px 12px;
}
.ev-details summary {
  cursor: pointer;
  color: #334155;
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 8px;
}
.ev-details[open] summary {
  margin-bottom: 10px;
}
@media (max-width: 900px) {
  .container {
    grid-template-columns: 1fr;
    grid-template-rows: 40vh 1fr;
  }
}
</style>
</head>
<body>
<header>
  <h1>Claude Sessions Viewer</h1>
  <small id="roots"></small>
</header>
<div class="container">
  <aside class="left">
    <div class="toolbar">
      <input id="project_q" placeholder="project/path (部分一致)" />
      <input id="date_from" type="date" />
      <input id="date_to" type="date" />
      <input id="q" placeholder="keyword filter" />
      <select id="source_filter">
        <option value="">source: all</option>
        <option value="claude_cli">Claude Code CLI</option>
        <option value="claude_desktop">Claude Desktop</option>
      </select>
      <select id="mode">
        <option value="and">keyword AND</option>
        <option value="or">keyword OR</option>
      </select>
      <button id="reload">Reload</button>
    </div>
    <div id="sessions"></div>
  </aside>
  <main class="right">
    <div class="meta" id="meta">セッションを選択してください</div>
    <div class="detail-toolbar">
      <label><input type="checkbox" id="only_user_instruction" /> ユーザー指示のみ表示</label>
      <label><input type="checkbox" id="only_ai_response" /> AIレスポンスのみ表示</label>
      <label><input type="checkbox" id="reverse_order" /> 表示順を逆にする</label>
      <button id="copy_resume_cmd" type="button" disabled>セッション再開コマンドコピー</button>
    </div>
    <div id="events"></div>
  </main>
</div>
<script>
const state = {
  sessions: [],
  filtered: [],
  activePath: null,
  activeSession: null,
  activeEvents: [],
  activeRawLineCount: 0,
};

function esc(s){
  return (s ?? '').toString().replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
}

function normalizePathForMatch(s){
  return (s ?? '')
    .toString()
    .toLowerCase()
    .replace(/[\\/]+/g, '-')
    .replace(/-+/g, '-')
    .trim();
}

function fmt(ts){
  if(!ts) return '';
  const d = new Date(ts);
  return isNaN(d) ? ts : d.toLocaleString();
}

function toTimestamp(ts){
  if(!ts) return NaN;
  const d = new Date(ts);
  return d.getTime();
}

function parseOptionalDateStart(raw){
  if(!raw) return null;
  const ts = toTimestamp(`${raw}T00:00:00`);
  return Number.isNaN(ts) ? null : ts;
}

function parseOptionalDateEnd(raw){
  if(!raw) return null;
  const ts = toTimestamp(`${raw}T23:59:59.999`);
  return Number.isNaN(ts) ? null : ts;
}

async function loadSessions(){
  const r = await fetch('/api/sessions');
  const data = await r.json();
  state.sessions = data.sessions;
  document.getElementById('roots').textContent =
    `CLI roots: ${data.roots.claude_cli.join(', ') || '-'} | Desktop roots: ${data.roots.claude_desktop.join(', ') || '-'}`;
  applyFilter();
}

function applyFilter(){
  const projectQ = document.getElementById('project_q').value.toLowerCase().trim();
  const q = document.getElementById('q').value.toLowerCase().trim();
  const fromRaw = document.getElementById('date_from').value;
  const toRaw = document.getElementById('date_to').value;
  const sourceFilter = document.getElementById('source_filter').value;
  const fromTs = parseOptionalDateStart(fromRaw);
  const toTs = parseOptionalDateEnd(toRaw);
  const mode = document.getElementById('mode').value;
  const terms = q.split(/\\s+/).filter(Boolean);

  state.filtered = state.sessions.filter(s => {
    const projectTarget = ((s.project || '') + ' ' + (s.relative_path || '')).toLowerCase();
    const projectTargetNorm = normalizePathForMatch(projectTarget);
    const projectQNorm = normalizePathForMatch(projectQ);
    const projectMatched =
      !projectQ ||
      projectTarget.includes(projectQ) ||
      (projectQNorm && projectTargetNorm.includes(projectQNorm));
    const sourceMatched = !sourceFilter || s.source_type === sourceFilter;

    let dateMatched = true;
    if(fromTs !== null || toTs !== null){
      const sessionTs = toTimestamp(s.started_at || s.mtime);
      if(Number.isNaN(sessionTs)){
        dateMatched = false;
      } else {
        if(fromTs !== null && sessionTs < fromTs) dateMatched = false;
        if(toTs !== null && sessionTs > toTs) dateMatched = false;
      }
    }

    let keywordMatched = true;
    if(terms.length > 0){
      const target = (
        (s.relative_path || '') + ' ' +
        (s.project || '') + ' ' +
        (s.first_user_text || '') + ' ' +
        (s.search_text || '')
      ).toLowerCase();
      if(mode === 'or'){
        keywordMatched = terms.some(t => target.includes(t));
      } else {
        keywordMatched = terms.every(t => target.includes(t));
      }
    }
    return projectMatched && sourceMatched && dateMatched && keywordMatched;
  });
  renderSessionList();
}

function renderSessionList(){
  const box = document.getElementById('sessions');
  box.innerHTML = state.filtered.map(s => `
    <div class="session-item ${state.activePath === s.path ? 'active' : ''}" data-path="${esc(s.path)}" data-source="${esc(s.source_type)}">
      <div class="session-path">${esc(s.relative_path)}</div>
      <div class="session-preview">${esc(s.first_user_text || '(previewなし)')}</div>
      <div class="session-tags">
        <span class="session-project">project: ${esc(s.project || '-')}</span>
        <span class="session-source ${s.source_type === 'claude_cli' ? 'cli' : 'desktop'}">${s.source_type === 'claude_cli' ? 'CLI(JSONL)' : 'Desktop(LevelDB)'}</span>
        <span class="session-time">${esc(fmt(s.started_at || s.mtime))}</span>
      </div>
    </div>
  `).join('');
  box.querySelectorAll('.session-item').forEach(el => {
    el.onclick = () => openSession(el.dataset.path, el.dataset.source);
  });
}

function getDisplayEvents(){
  let events = state.activeEvents || [];
  const showOnlyUser = document.getElementById('only_user_instruction').checked;
  const showOnlyAssistant = document.getElementById('only_ai_response').checked;
  if(showOnlyUser || showOnlyAssistant){
    events = events.filter(ev => {
      if(ev.kind !== 'message') return false;
      return (showOnlyUser && ev.role === 'user') || (showOnlyAssistant && ev.role === 'assistant');
    });
  }
  if(document.getElementById('reverse_order').checked){
    events = [...events].reverse();
  }
  return events;
}

function renderActiveSession(){
  const meta = document.getElementById('meta');
  const eventsBox = document.getElementById('events');
  if(!state.activeSession){
    meta.textContent = 'セッションを選択してください';
    eventsBox.innerHTML = '';
    updateCopyResumeButtonState();
    return;
  }

  updateCopyResumeButtonState();

  const displayEvents = getDisplayEvents();
  const sourceType = state.activeSession.source_type || '';
  const sourceClass = sourceType === 'claude_cli' ? 'cli' : 'desktop';
  meta.innerHTML =
    `source: <code class="source-code ${sourceClass}">${esc(state.activeSession.source)}</code> | path: <code class="path-code">${esc(state.activeSession.relative_path)}</code> | ` +
    `project: <code class="project-code">${esc(state.activeSession.project || '-')}</code> | ` +
    `events: ${displayEvents.length}/${state.activeEvents.length} | raw lines/snippets: ${state.activeRawLineCount}`;

  eventsBox.innerHTML = displayEvents.map(ev => {
    const role = ev.role || 'system';
    const roleLabel = ev.role_label || role;
    const kind = ev.kind || 'event';
    const safeKind = String(kind).replace(/[^a-zA-Z0-9_-]/g, '-').toLowerCase();
    const safeRole = String(role).replace(/[^a-zA-Z0-9_-]/g, '-').toLowerCase();
    const rawText = ev.text || '';
    const lineCount = rawText ? rawText.split('\\n').length : 0;
    const isLong = rawText.length > 1800 || lineCount > 35;
    const labels = Array.isArray(ev.labels) ? ev.labels : [];
    const labelsHtml = labels.length
      ? `<span class="ev-badges">${labels.map(v => `<span class="ev-badge">[${esc(String(v))}]</span>`).join('')}</span>`
      : '';
    const body = isLong
      ? `<details class="ev-details"><summary>本文を展開 (${lineCount} lines)</summary><pre>${esc(rawText)}</pre></details>`
      : `<pre>${esc(rawText)}</pre>`;
    return `<div class="ev ${safeRole} kind-${safeKind}"><div class="ev-head"><span>${esc(kind)}</span><span class="ev-role ${esc(safeRole)}">${esc(roleLabel)}</span><span>${esc(fmt(ev.timestamp))}</span>${labelsHtml}</div>${body}</div>`;
  }).join('');
}

async function openSession(path, sourceType){
  state.activePath = path;
  renderSessionList();
  const r = await fetch('/api/session?path=' + encodeURIComponent(path) + '&source=' + encodeURIComponent(sourceType || ''));
  const data = await r.json();
  if(data.error){
    state.activeSession = null;
    state.activeEvents = [];
    state.activeRawLineCount = 0;
    document.getElementById('meta').textContent = data.error;
    document.getElementById('events').innerHTML = '';
    updateCopyResumeButtonState();
    return;
  }
  state.activeSession = data.session;
  state.activeEvents = data.events || [];
  state.activeRawLineCount = data.raw_line_count || 0;
  renderActiveSession();
}

function getActiveSessionId(){
  if(!state.activeSession) return '';
  const sid = state.activeSession.id || '';
  if(sid) return sid;
  const rel = state.activeSession.relative_path || '';
  const m = rel.match(/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/);
  return m ? m[0] : '';
}

function updateCopyResumeButtonState(){
  const copyBtn = document.getElementById('copy_resume_cmd');
  copyBtn.disabled = !getActiveSessionId();
}

async function copyResumeCommand(){
  const sid = getActiveSessionId();
  if(!sid) return;
  const cmd = `claude --resume ${sid}`;
  let copied = false;
  try {
    if(navigator.clipboard && navigator.clipboard.writeText){
      await navigator.clipboard.writeText(cmd);
      copied = true;
    }
  } catch(_err) {
    copied = false;
  }

  if(!copied){
    const ta = document.createElement('textarea');
    ta.value = cmd;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
      copied = document.execCommand('copy');
    } finally {
      document.body.removeChild(ta);
    }
  }

  if(copied){
    const btn = document.getElementById('copy_resume_cmd');
    const old = btn.textContent;
    btn.textContent = 'コピーしました';
    setTimeout(() => { btn.textContent = old; }, 1200);
  }
}

document.getElementById('project_q').addEventListener('input', applyFilter);
document.getElementById('date_from').addEventListener('change', applyFilter);
document.getElementById('date_to').addEventListener('change', applyFilter);
document.getElementById('q').addEventListener('input', applyFilter);
document.getElementById('source_filter').addEventListener('change', applyFilter);
document.getElementById('mode').addEventListener('change', applyFilter);
document.getElementById('reload').addEventListener('click', loadSessions);
document.getElementById('only_user_instruction').addEventListener('change', renderActiveSession);
document.getElementById('only_ai_response').addEventListener('change', renderActiveSession);
document.getElementById('reverse_order').addEventListener('change', renderActiveSession);
document.getElementById('copy_resume_cmd').addEventListener('click', copyResumeCommand);
updateCopyResumeButtonState();
loadSessions();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, text, status=200):
        raw = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send_html(HTML_PAGE)
            return

        if parsed.path == "/api/sessions":
            roots = get_roots()
            items = iter_all_session_files()[:MAX_LIST]
            sessions = [summarize_session(source_type, path, root) for source_type, path, root in items]
            self._send_json(
                {
                    "roots": {
                        "claude_cli": [str(x) for x in roots["claude_cli"]],
                        "claude_desktop": [str(x) for x in roots["claude_desktop"]],
                    },
                    "sessions": sessions,
                }
            )
            return

        if parsed.path == "/api/session":
            q = urllib.parse.parse_qs(parsed.query)
            raw_path = q.get("path", [""])[0]
            source_type = q.get("source", [""])[0]
            if not raw_path:
                self._send_json({"error": "path is required"}, 400)
                return
            p = Path(raw_path).expanduser().resolve()
            roots = get_roots()
            allowed_roots = roots["claude_cli"] + roots["claude_desktop"]
            if source_type not in ("claude_cli", "claude_desktop"):
                source_type = "claude_cli" if any("projects" in str(r).lower() for r in allowed_roots) else "claude_desktop"

            chosen_root = None
            for root in allowed_roots:
                try:
                    p.relative_to(root.resolve())
                    chosen_root = root
                    break
                except Exception:
                    continue
            if chosen_root is None:
                self._send_json({"error": "path is outside allowed roots"}, 400)
                return
            if not p.exists() or not p.is_file():
                self._send_json({"error": "session file not found"}, 404)
                return

            session = summarize_session(source_type, p, chosen_root)
            data = load_session_events(source_type, p)
            data["session"] = session
            self._send_json(data)
            return

        self._send_html("<h1>404</h1>", 404)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Viewer: http://{HOST}:{PORT}")
    print("Claude Code CLI roots:")
    for p in get_claude_cli_roots():
        print(f"  - {p}")
    print("Claude Desktop roots:")
    for p in get_claude_desktop_roots():
        print(f"  - {p}")
    server.serve_forever()


if __name__ == "__main__":
    main()
