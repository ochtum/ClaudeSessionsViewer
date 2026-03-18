#!/usr/bin/env python3
import functools
import json
import locale
import os
import re
import shlex
import sqlite3
import struct
import subprocess
import threading
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
SEARCH_INDEX_TEXT_LIMIT = SEARCH_TEXT_LIMIT
SEARCH_INDEX_SCHEMA_VERSION = 4
SEARCH_INDEX_DB_PATH = Path(__file__).resolve().parent / ".cache" / "search_index.sqlite3"
ICON_DIR = Path(__file__).resolve().parent / 'icons'
MAX_JSON_BODY_BYTES = 1_000_000
_SESSION_CACHE = {}
_SESSION_CACHE_LOCK = threading.Lock()
_SEARCH_INDEX_LOCK = threading.Lock()
LABEL_COLOR_PRESETS = {
    "red": "#ef4444",
    "blue": "#3b82f6",
    "green": "#22c55e",
    "yellow": "#eab308",
    "purple": "#a855f7",
}
LABEL_COLOR_FAMILY_LABELS = {
    "red": "赤系",
    "blue": "青系",
    "green": "緑系",
    "yellow": "黄色系",
    "purple": "紫系",
}


def _unique_paths(paths):
    out = []
    seen = set()
    for p in paths:
        key = session_path_key(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _path_exists_safe(path: Path) -> bool:
    try:
        return path.exists()
    except Exception:
        return False


def windows_path_to_wsl(path_str: str):
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", path_str)
    if not m:
        return None
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/").lstrip("/")
    return Path("/mnt") / drive / rest


def canonicalize_path(path) -> Path:
    candidate = Path(path).expanduser()
    if isinstance(path, str):
        converted = windows_path_to_wsl(path)
        if converted is not None and not _path_exists_safe(candidate):
            candidate = converted
    try:
        return candidate.resolve(strict=False)
    except TypeError:
        return candidate.resolve()
    except Exception:
        return candidate.absolute()


def session_path_key(path) -> str:
    return str(canonicalize_path(path))


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


@functools.lru_cache(maxsize=1)
def get_canonical_roots():
    roots = get_roots()
    return {
        "claude_cli": _unique_paths(canonicalize_path(root) for root in roots["claude_cli"]),
        "claude_desktop": _unique_paths(canonicalize_path(root) for root in roots["claude_desktop"]),
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
    unique = {}
    for source_type, path, root in files:
        unique[session_path_key(path)] = (source_type, canonicalize_path(path), canonicalize_path(root))
    return list(unique.values())


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


def _is_continuation_summary_text(text: str) -> bool:
    if not isinstance(text, str):
        return False
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    if not normalized:
        return False
    if "this session is being continued from a previous conversation that ran out of context." not in normalized:
        return False
    return (
        "the summary below covers the earlier portion of the conversation." in normalized
        or "summary:" in normalized
    )


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
    path = canonicalize_path(path)
    root = canonicalize_path(root)
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
    path = canonicalize_path(path)
    root = canonicalize_path(root)
    summary = {
        "id": path.stem,
        "path": session_path_key(path),
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
    path = canonicalize_path(path)
    root = canonicalize_path(root)
    summary = {
        "id": path.name,
        "path": session_path_key(path),
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


def normalize_search_text(text: str) -> str:
    if not text:
        return ""
    normalized = str(text).replace("\\", "/")
    return re.sub(r"\s+", " ", normalized).strip().lower()


def parse_search_query(query: str) -> list[str]:
    """Split a search query while preserving quoted phrases."""
    try:
        return shlex.split(query)
    except ValueError:
        return query.split()


def is_safe_css_color(value: str) -> bool:
    candidate = (value or "").strip()
    if not candidate or len(candidate) > 64:
        return False
    if not re.fullmatch(r"[#(),.%/\-\sa-zA-Z0-9]+", candidate):
        return False
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", candidate):
        return True
    lowered = candidate.lower()
    if re.fullmatch(r"rgba?\([^()]+\)", lowered):
        return True
    if re.fullmatch(r"oklch\([^()]+\)", lowered):
        return True
    return False


def normalize_label_color(color_value: str, color_family: str):
    family = (color_family or "").strip().lower()
    if family not in LABEL_COLOR_PRESETS:
        family = ""
    value = (color_value or "").strip()
    if value:
        if not is_safe_css_color(value):
            raise ValueError("色コードの形式が不正です")
        return value, family
    if family:
        return LABEL_COLOR_PRESETS[family], family
    raise ValueError("色コードを入力してください")


def parse_optional_int(raw):
    try:
        if raw is None or raw == "":
            return None
        return int(raw)
    except (TypeError, ValueError):
        return None


def parse_json_body(handler):
    length = parse_optional_int(handler.headers.get("Content-Length"))
    if not length or length < 0:
        return {}
    if length > MAX_JSON_BODY_BYTES:
        raise ValueError("request body too large")
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def append_search_chunk(chunks, text: str, current_len: int, limit: int):
    normalized = normalize_search_text(text)
    unlimited = limit <= 0
    if not normalized or (not unlimited and current_len >= limit):
        return current_len
    if not unlimited:
        remaining = limit - current_len
        if len(normalized) > remaining:
            normalized = normalized[:remaining]
    chunks.append(normalized)
    return current_len + len(normalized)


def get_session_signature(path: Path, stat_result=None, signature=None):
    st = stat_result if stat_result is not None else path.stat()
    sig = signature if signature is not None else (st.st_mtime_ns, st.st_size)
    return st, sig


def set_cached_summary(path_key: str, signature, summary):
    with _SESSION_CACHE_LOCK:
        entry = _SESSION_CACHE.get(path_key)
        if not entry or entry.get("signature") != signature:
            entry = {"signature": signature, "summary": None, "events": None}
            _SESSION_CACHE[path_key] = entry
        entry["summary"] = summary


def load_legacy_label_link_rows(conn):
    session_links = set()
    event_links = set()
    try:
        rows = conn.execute("SELECT session_path, label_id FROM session_label_links").fetchall()
    except sqlite3.OperationalError:
        rows = []
    for row in rows:
        session_path = row["session_path"] if row["session_path"] is not None else ""
        label_id = row["label_id"]
        if not session_path:
            continue
        session_links.add((session_path_key(session_path), label_id))
    try:
        rows = conn.execute("SELECT session_path, event_id, label_id FROM event_label_links").fetchall()
    except sqlite3.OperationalError:
        rows = []
    for row in rows:
        session_path = row["session_path"] if row["session_path"] is not None else ""
        event_id = row["event_id"]
        label_id = row["label_id"]
        if not session_path or not event_id:
            continue
        event_links.add((session_path_key(session_path), event_id, label_id))
    return sorted(session_links), sorted(event_links)


def open_search_index_connection():
    SEARCH_INDEX_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SEARCH_INDEX_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    row = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
    current_version = parse_optional_int(row["value"]) if row is not None else 0
    if current_version is None:
        current_version = 0
    migrated_session_links = []
    migrated_event_links = []
    if current_version < 3:
        migrated_session_links, migrated_event_links = load_legacy_label_link_rows(conn)
        with conn:
            conn.execute("DROP TABLE IF EXISTS session_index")
            conn.execute("DROP TABLE IF EXISTS session_label_links")
            conn.execute("DROP TABLE IF EXISTS event_label_links")
    if 3 <= current_version < 4:
        with conn:
            conn.execute("DROP TABLE IF EXISTS session_index")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_index (
            path TEXT PRIMARY KEY,
            id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            mtime_iso TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            cwd TEXT NOT NULL,
            model TEXT NOT NULL,
            source TEXT NOT NULL,
            source_type TEXT NOT NULL,
            project TEXT NOT NULL,
            first_user_text TEXT NOT NULL,
            search_text TEXT NOT NULL,
            min_event_ts TEXT NOT NULL DEFAULT '',
            max_event_ts TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session_index_mtime_ns ON session_index (mtime_ns DESC)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            color_value TEXT NOT NULL,
            color_family TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_label_links (
            session_path TEXT NOT NULL,
            label_id INTEGER NOT NULL,
            PRIMARY KEY (session_path, label_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS event_label_links (
            session_path TEXT NOT NULL,
            event_id TEXT NOT NULL,
            label_id INTEGER NOT NULL,
            PRIMARY KEY (session_path, event_id, label_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session_label_links_label ON session_label_links (label_id, session_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_label_links_label ON event_label_links (label_id, session_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_label_links_session ON event_label_links (session_path, event_id)")
    if migrated_session_links or migrated_event_links:
        with conn:
            if migrated_session_links:
                conn.executemany(
                    "INSERT OR IGNORE INTO session_label_links (session_path, label_id) VALUES (?, ?)",
                    migrated_session_links,
                )
            if migrated_event_links:
                conn.executemany(
                    "INSERT OR IGNORE INTO event_label_links (session_path, event_id, label_id) VALUES (?, ?, ?)",
                    migrated_event_links,
                )
    if current_version != SEARCH_INDEX_SCHEMA_VERSION:
        with conn:
            conn.execute(
                """
                INSERT INTO app_meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("schema_version", str(SEARCH_INDEX_SCHEMA_VERSION)),
            )
    return conn


def label_row_to_dict(row):
    family = row["color_family"] or ""
    return {
        "id": row["id"],
        "name": row["name"],
        "color_value": row["color_value"],
        "color_family": family,
        "color_family_label": LABEL_COLOR_FAMILY_LABELS.get(family, ""),
    }


def list_labels():
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, color_value, color_family FROM labels ORDER BY name COLLATE NOCASE ASC, id ASC"
            ).fetchall()
            return [label_row_to_dict(row) for row in rows]
        finally:
            conn.close()


def save_label(label_id, name: str, color_value: str, color_family: str):
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("ラベル名を入力してください")
    if len(clean_name) > 60:
        raise ValueError("ラベル名が長すぎます")
    normalized_color, normalized_family = normalize_label_color(color_value, color_family)
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                if label_id is None:
                    cur = conn.execute(
                        "INSERT INTO labels (name, color_value, color_family) VALUES (?, ?, ?)",
                        (clean_name, normalized_color, normalized_family),
                    )
                    saved_id = cur.lastrowid
                else:
                    conn.execute(
                        "UPDATE labels SET name = ?, color_value = ?, color_family = ? WHERE id = ?",
                        (clean_name, normalized_color, normalized_family, label_id),
                    )
                    saved_id = label_id
                row = conn.execute(
                    "SELECT id, name, color_value, color_family FROM labels WHERE id = ?",
                    (saved_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("ラベルが見つかりません")
                return label_row_to_dict(row)
        except sqlite3.IntegrityError:
            raise ValueError("同名のラベルは既に存在します")
        finally:
            conn.close()


def delete_label(label_id):
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                conn.execute("DELETE FROM session_label_links WHERE label_id = ?", (label_id,))
                conn.execute("DELETE FROM event_label_links WHERE label_id = ?", (label_id,))
                conn.execute("DELETE FROM labels WHERE id = ?", (label_id,))
        finally:
            conn.close()


def fetch_session_labels_map(paths, conn):
    unique_paths = [session_path_key(path) for path in paths if path]
    if not unique_paths:
        return {}
    placeholders = ", ".join("?" for _ in unique_paths)
    rows = conn.execute(
        f"""
        SELECT sl.session_path, l.id, l.name, l.color_value, l.color_family
        FROM session_label_links sl
        JOIN labels l ON l.id = sl.label_id
        WHERE sl.session_path IN ({placeholders})
        ORDER BY l.name COLLATE NOCASE ASC, l.id ASC
        """,
        unique_paths,
    ).fetchall()
    mapping = {path: [] for path in unique_paths}
    for row in rows:
        mapping.setdefault(row["session_path"], []).append(label_row_to_dict(row))
    return mapping


def fetch_event_labels_map(session_path, conn):
    rows = conn.execute(
        """
        SELECT el.event_id, l.id, l.name, l.color_value, l.color_family
        FROM event_label_links el
        JOIN labels l ON l.id = el.label_id
        WHERE el.session_path = ?
        ORDER BY l.name COLLATE NOCASE ASC, l.id ASC
        """,
        (session_path_key(session_path),),
    ).fetchall()
    mapping = {}
    for row in rows:
        mapping.setdefault(row["event_id"], []).append(label_row_to_dict(row))
    return mapping


def assign_session_label(session_path, label_id: int):
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO session_label_links (session_path, label_id)
                    SELECT ?, id FROM labels WHERE id = ?
                    """,
                    (session_path_key(session_path), label_id),
                )
        finally:
            conn.close()


def remove_session_label(session_path, label_id: int):
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM session_label_links WHERE session_path = ? AND label_id = ?",
                    (session_path_key(session_path), label_id),
                )
        finally:
            conn.close()


def assign_event_label(session_path, event_id: str, label_id: int):
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO event_label_links (session_path, event_id, label_id)
                    SELECT ?, ?, id FROM labels WHERE id = ?
                    """,
                    (session_path_key(session_path), event_id, label_id),
                )
        finally:
            conn.close()


def remove_event_label(session_path, event_id: str, label_id: int):
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM event_label_links WHERE session_path = ? AND event_id = ? AND label_id = ?",
                    (session_path_key(session_path), event_id, label_id),
                )
        finally:
            conn.close()


def summary_from_index_row(row):
    return {
        "id": row["id"],
        "path": row["path"],
        "relative_path": row["relative_path"],
        "mtime": row["mtime_iso"],
        "session_id": row["session_id"],
        "started_at": row["started_at"],
        "cwd": row["cwd"],
        "model": row["model"],
        "source": row["source"],
        "source_type": row["source_type"],
        "project": row["project"],
        "first_user_text": row["first_user_text"],
        "min_event_ts": row["min_event_ts"],
        "max_event_ts": row["max_event_ts"],
    }


def _search_prefix_from_summary(summary):
    values = [
        summary.get("relative_path", ""),
        summary.get("project", ""),
        summary.get("cwd", ""),
        summary.get("source", ""),
        summary.get("source_type", ""),
        summary.get("first_user_text", ""),
    ]
    out = []
    for value in values:
        normalized = normalize_search_text(value)
        if normalized:
            out.append(normalized)
    return out


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
                text = f"{obj.get('operation', '')}\n{obj.get('content', '')}".strip()
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
            system_labels = []
            if kind == "message" and role == "user" and _is_skills_instruction_text(text):
                system_labels.append("SKILLS")
            if kind == "message" and role == "user" and _is_continuation_summary_text(text):
                system_labels.append("CONTINUATION_SUMMARY")
            event = {
                "event_id": f"line-{raw_count}",
                "timestamp": ts,
                "kind": kind,
                "role": role,
                "text": text,
            }
            if system_labels:
                event["system_labels"] = system_labels
            events.append(event)
            if len(events) >= MAX_EVENTS:
                break
    return {"events": events, "raw_line_count": raw_count}


def load_desktop_events(path: Path):
    events = []
    if path.suffix == ".log":
        entries = _ldb_parse_desktop_entries(path)
        if entries:
            notice = (
                "Claude Desktop の IndexedDB(LevelDB) から chat-draft エントリを解析しました。"
                " これらは送信前の下書きメッセージです。送信済み会話は claude.ai サーバー側に保存されています。"
            )
            events.append({"event_id": "notice-0", "timestamp": "", "kind": "notice", "role": "system", "text": notice})
            for idx, entry in enumerate(entries, start=1):
                key = entry["idb_key"]
                parts = key.split(":")
                conv_label = parts[-1] if len(parts) >= 3 else key
                attach_note = f"  [添付 {entry['attach_count']} 件]" if entry["attach_count"] else ""
                text = f"[{conv_label}]\n{entry['text']}{attach_note}"
                events.append(
                    {
                        "event_id": f"entry-{idx}",
                        "timestamp": entry["updated_at"],
                        "kind": "message",
                        "role": "user",
                        "text": text,
                    }
                )
            return {"events": events[:MAX_EVENTS], "raw_line_count": len(entries)}

    with path.open("rb") as f:
        raw = f.read(min(MAX_DESKTOP_SCAN_BYTES, max(256 * 1024, path.stat().st_size)))

    objs = _extract_json_objects_from_bytes(raw, limit=MAX_EVENTS)
    if objs:
        for idx, obj in enumerate(objs):
            text = "\n".join(_extract_text_recursive(obj)).strip()
            if not text:
                continue
            events.append(
                {
                    "event_id": f"snippet-{idx}",
                    "timestamp": _extract_ts_from_obj(obj),
                    "kind": "snippet",
                    "role": _guess_role(obj),
                    "text": text[:4000],
                }
            )
    else:
        for idx, snippet in enumerate(_extract_readable_snippets(raw, limit=800)):
            events.append({"event_id": f"snippet-{idx}", "timestamp": "", "kind": "snippet", "role": "system", "text": snippet})

    notice = (
        "Claude Desktop の IndexedDB(LevelDB) はバイナリ形式のため、ここでは文字列/JSONスニペット抽出で表示しています。"
        " 完全な履歴復元ではありません。"
    )
    events.insert(0, {"event_id": "notice-0", "timestamp": "", "kind": "notice", "role": "system", "text": notice})
    return {"events": events[:MAX_EVENTS], "raw_line_count": len(events)}


def build_search_index_record(source_type: str, path: Path, root: Path, stat_result=None):
    path = canonicalize_path(path)
    root = canonicalize_path(root)
    st = stat_result if stat_result is not None else path.stat()
    if source_type == "claude_cli":
        summary = summarize_cli_session(path, root)
        event_data = load_cli_events(path)
    else:
        summary = summarize_desktop_blob(path, root)
        event_data = load_desktop_events(path)
    summary["path"] = session_path_key(path)
    summary["mtime"] = datetime.fromtimestamp(st.st_mtime).isoformat()
    summary["session_id"] = summary.get("id", "")
    search_chunks = []
    search_len = 0
    min_event_ts = ""
    max_event_ts = ""
    for event in event_data["events"]:
        event_ts = _iso_from_ts(event.get("timestamp"))
        if event_ts:
            if not min_event_ts or event_ts < min_event_ts:
                min_event_ts = event_ts
            if not max_event_ts or event_ts > max_event_ts:
                max_event_ts = event_ts
        search_len = append_search_chunk(search_chunks, event.get("text", ""), search_len, SEARCH_INDEX_TEXT_LIMIT)
        for label in event.get("system_labels", []):
            search_len = append_search_chunk(search_chunks, label, search_len, SEARCH_INDEX_TEXT_LIMIT)
    search_text = " ".join(_search_prefix_from_summary(summary) + search_chunks)
    summary["min_event_ts"] = min_event_ts
    summary["max_event_ts"] = max_event_ts
    return summary, search_text


def sync_search_index(items, prune_missing=True):
    current = {}
    for source_type, raw_path, raw_root in items:
        path = canonicalize_path(raw_path)
        root = canonicalize_path(raw_root)
        try:
            stat_result, signature = get_session_signature(path)
        except FileNotFoundError:
            continue
        path_key = session_path_key(path)
        if path_key in current:
            continue
        current[path_key] = {
            "source_type": source_type,
            "path": path,
            "root": root,
            "stat_result": stat_result,
            "signature": signature,
        }
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            rows = conn.execute("SELECT path, mtime_ns, size FROM session_index").fetchall()
            existing = {row["path"]: (row["mtime_ns"], row["size"]) for row in rows}
        finally:
            conn.close()

    stale_paths = (
        [
            path_key
            for path_key in existing
            if path_key not in current and not _path_exists_safe(canonicalize_path(path_key))
        ]
        if prune_missing
        else []
    )
    changed = []
    for path_key, item in current.items():
        if existing.get(path_key) != item["signature"]:
            changed.append(item)

    changed_records = []
    for item in changed:
        summary, search_text = build_search_index_record(
            item["source_type"],
            item["path"],
            item["root"],
            stat_result=item["stat_result"],
        )
        changed_records.append((item["path"], item["signature"], summary, search_text))

    if not stale_paths and not changed_records:
        return

    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                if stale_paths:
                    conn.executemany("DELETE FROM session_index WHERE path = ?", ((path_key,) for path_key in stale_paths))
                    conn.executemany("DELETE FROM session_label_links WHERE session_path = ?", ((path_key,) for path_key in stale_paths))
                    conn.executemany("DELETE FROM event_label_links WHERE session_path = ?", ((path_key,) for path_key in stale_paths))
                for _, signature, summary, search_text in changed_records:
                    conn.execute(
                        """
                        INSERT INTO session_index (
                            path, id, relative_path, mtime_iso, mtime_ns, size,
                            session_id, started_at, cwd, model, source, source_type,
                            project, first_user_text, search_text, min_event_ts, max_event_ts
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(path) DO UPDATE SET
                            id = excluded.id,
                            relative_path = excluded.relative_path,
                            mtime_iso = excluded.mtime_iso,
                            mtime_ns = excluded.mtime_ns,
                            size = excluded.size,
                            session_id = excluded.session_id,
                            started_at = excluded.started_at,
                            cwd = excluded.cwd,
                            model = excluded.model,
                            source = excluded.source,
                            source_type = excluded.source_type,
                            project = excluded.project,
                            first_user_text = excluded.first_user_text,
                            search_text = excluded.search_text,
                            min_event_ts = excluded.min_event_ts,
                            max_event_ts = excluded.max_event_ts
                        """,
                        (
                            summary["path"],
                            summary["id"],
                            summary["relative_path"],
                            summary["mtime"],
                            signature[0],
                            signature[1],
                            summary["session_id"],
                            summary.get("started_at", ""),
                            summary.get("cwd", ""),
                            summary.get("model", ""),
                            summary["source"],
                            summary["source_type"],
                            summary.get("project", ""),
                            summary.get("first_user_text", ""),
                            search_text,
                            summary.get("min_event_ts", ""),
                            summary.get("max_event_ts", ""),
                        ),
                    )
        finally:
            conn.close()

    for path, signature, summary, _ in changed_records:
        set_cached_summary(session_path_key(path), signature, summary)


def fetch_sessions_from_search_index(query: str, mode: str, limit: int, session_label_id=None, event_label_id=None, sort="desc"):
    normalized_terms = []
    for term in parse_search_query(query):
        normalized = normalize_search_text(term)
        if normalized:
            normalized_terms.append(normalized)
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            columns = (
                "id, path, relative_path, mtime_iso, session_id, started_at, "
                "cwd, model, source, source_type, project, first_user_text, "
                "min_event_ts, max_event_ts"
            )
            where_clauses = []
            params = []
            if normalized_terms:
                joiner = " OR " if mode == "or" else " AND "
                keyword_clause = joiner.join("instr(search_text, ?) > 0" for _ in normalized_terms)
                where_clauses.append(f"({keyword_clause})")
                params.extend(normalized_terms)
            if session_label_id is not None:
                where_clauses.append(
                    "EXISTS (SELECT 1 FROM session_label_links sl WHERE sl.session_path = session_index.path AND sl.label_id = ?)"
                )
                params.append(session_label_id)
            if event_label_id is not None:
                where_clauses.append(
                    "EXISTS (SELECT 1 FROM event_label_links el WHERE el.session_path = session_index.path AND el.label_id = ?)"
                )
                params.append(event_label_id)
            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            if sort == "updated":
                order_sql = "ORDER BY mtime_ns DESC"
            else:
                direction = "ASC" if sort == "asc" else "DESC"
                order_sql = (
                    "ORDER BY "
                    f"CASE WHEN started_at IS NOT NULL AND started_at <> '' THEN started_at ELSE mtime_iso END {direction}, "
                    f"mtime_ns {direction}"
                )
            sql = f"SELECT {columns} FROM session_index {where_sql} {order_sql} LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            sessions = [summary_from_index_row(row) for row in rows]
            label_map = fetch_session_labels_map([session["path"] for session in sessions], conn)
            for session in sessions:
                session["session_labels"] = label_map.get(session["path"], [])
            return sessions
        finally:
            conn.close()


def fetch_session_summary_from_index(path_key: str):
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            row = conn.execute(
                """
                SELECT id, path, relative_path, mtime_iso, session_id, started_at,
                       cwd, model, source, source_type, project, first_user_text,
                       min_event_ts, max_event_ts
                FROM session_index
                WHERE path = ?
                """,
                (path_key,),
            ).fetchone()
            if row is None:
                return None
            summary = summary_from_index_row(row)
            summary["session_labels"] = fetch_session_labels_map([summary["path"]], conn).get(summary["path"], [])
            return summary
        finally:
            conn.close()


def summarize_session(source_type: str, path: Path, root: Path, stat_result=None, signature=None):
    st, sig = get_session_signature(path, stat_result, signature)
    key = session_path_key(path)
    with _SESSION_CACHE_LOCK:
        entry = _SESSION_CACHE.get(key)
        if entry and entry.get("signature") == sig and entry.get("summary") is not None:
            summary = dict(entry["summary"])
        else:
            summary = None
    if summary is None:
        if source_type == "claude_cli":
            summary = summarize_cli_session(path, root)
        else:
            summary = summarize_desktop_blob(path, root)
        summary["path"] = session_path_key(path)
        summary["session_id"] = summary.get("id", "")
        set_cached_summary(key, sig, summary)
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            summary["session_labels"] = fetch_session_labels_map([summary["path"]], conn).get(summary["path"], [])
        finally:
            conn.close()
    return summary


def load_session_events(source_type: str, path: Path, stat_result=None, signature=None):
    _, sig = get_session_signature(path, stat_result, signature)
    key = session_path_key(path)
    with _SESSION_CACHE_LOCK:
        entry = _SESSION_CACHE.get(key)
        if entry and entry.get("signature") == sig and entry.get("events") is not None:
            data = entry["events"]
        else:
            data = None
    if data is None:
        data = load_cli_events(path) if source_type == "claude_cli" else load_desktop_events(path)
        with _SESSION_CACHE_LOCK:
            entry = _SESSION_CACHE.get(key)
            if not entry or entry.get("signature") != sig:
                entry = {"signature": sig, "summary": None, "events": None}
                _SESSION_CACHE[key] = entry
            entry["events"] = data
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            label_map = fetch_event_labels_map(path, conn)
        finally:
            conn.close()
    decorated = []
    for event in data["events"]:
        cloned = dict(event)
        cloned["labels"] = label_map.get(event.get("event_id", ""), [])
        decorated.append(cloned)
    return {"events": decorated, "raw_line_count": data["raw_line_count"]}


def resolve_session_item(raw_path: str, source_type: str = ""):
    if not raw_path:
        raise ValueError("path is required")
    path = canonicalize_path(raw_path)
    requested = source_type if source_type in ("claude_cli", "claude_desktop") else ""
    roots = get_canonical_roots()
    source_order = [requested] if requested else ["claude_cli", "claude_desktop"]
    for candidate_source in source_order:
        for root in roots.get(candidate_source, []):
            try:
                path.relative_to(root)
            except Exception:
                continue
            if not _path_exists_safe(path) or not path.is_file():
                raise ValueError("session file not found")
            return candidate_source, path, root
    raise ValueError("path is outside allowed roots")


HTML_PAGE = """<!doctype html>
<html lang=\"ja\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>Claude Sessions Viewer</title>
<link rel=\"icon\" href=\"/icons/claude-sessions-viewer.svg\" type=\"image/svg+xml\" />
<style>
:root {
  --bg: #edf4fb;
  --bg-strong: #e6eef8;
  --surface: rgba(255, 255, 255, 0.9);
  --surface-strong: rgba(255, 255, 255, 0.96);
  --surface-soft: rgba(255, 255, 255, 0.68);
  --line: rgba(148, 163, 184, 0.24);
  --line-strong: rgba(148, 163, 184, 0.46);
  --text: #102033;
  --muted: #5b6b7c;
  --accent: #0f766e;
  --accent-strong: #0b5c57;
  --accent-soft: rgba(15, 118, 110, 0.12);
  --info: #1d4ed8;
  --info-soft: rgba(29, 78, 216, 0.1);
  --support: #7c3aed;
  --support-soft: rgba(124, 58, 237, 0.1);
  --danger: #be123c;
  --danger-soft: rgba(190, 18, 60, 0.1);
  --shadow-soft: 0 14px 30px rgba(15, 23, 42, 0.06);
  --shadow-medium: 0 24px 46px rgba(15, 23, 42, 0.09);
  --user: #2563eb;
  --assistant: #0f766e;
  --dev: #b45309;
  --system: #64748b;
  --sidebar-width: 320px;
  --font-sans: "Aptos", "Segoe UI", "Yu Gothic UI", sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  --text-kicker: 10px;
  --text-label: 11px;
  --text-caption: 12px;
  --text-body: 13px;
  --text-title-sm: 16px;
  --text-title-md: 18px;
  --text-title-lg: 20px;
  --text-display: clamp(28px, 1.55vw, 32px);
  --text-display-compact: 28px;
  --space-1: 4px;
  --space-2: 6px;
  --space-3: 8px;
  --space-4: 10px;
  --space-5: 12px;
  --space-6: 16px;
  --space-7: 18px;
  --space-8: 24px;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  position: relative;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-family: var(--font-sans);
  font-size: var(--text-body);
  line-height: 1.5;
  background:
    radial-gradient(circle at 10% 10%, rgba(59, 130, 246, 0.16), transparent 24%),
    radial-gradient(circle at 88% 14%, rgba(15, 118, 110, 0.14), transparent 22%),
    linear-gradient(180deg, #eef5fc 0%, var(--bg) 52%, var(--bg-strong) 100%);
  color: var(--text);
}
body::before,
body::after {
  content: "";
  position: fixed;
  width: 320px;
  height: 320px;
  border-radius: 999px;
  filter: blur(44px);
  pointer-events: none;
  opacity: 0.46;
}
body::before {
  top: -130px;
  left: -100px;
  background: rgba(96, 165, 250, 0.26);
}
body::after {
  right: -120px;
  bottom: -150px;
  background: rgba(15, 118, 110, 0.18);
}
header {
  position: relative;
  z-index: 2;
  padding: var(--space-5) var(--space-7);
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.68);
  backdrop-filter: blur(16px);
}
.header-main {
  position: relative;
  overflow: hidden;
  flex: 1 1 auto;
  min-width: 0;
  display: grid;
  gap: var(--space-1);
  padding: var(--space-4) var(--space-7);
  border: 1px solid rgba(190, 208, 233, 0.9);
  border-radius: 22px;
  background: linear-gradient(180deg, rgba(248, 251, 255, 0.98), rgba(234, 241, 251, 0.92));
  box-shadow: 0 14px 32px rgba(43, 87, 145, 0.11);
}
.header-main::before {
  content: "";
  position: absolute;
  inset: -48px auto auto -46px;
  width: 150px;
  height: 92px;
  border-radius: 999px;
  background: radial-gradient(circle, rgba(125, 211, 252, 0.14) 0%, rgba(125, 211, 252, 0) 72%);
  pointer-events: none;
}
.header-main::after {
  content: "";
  position: absolute;
  inset: auto -54px -56px auto;
  width: 180px;
  height: 120px;
  border-radius: 999px;
  background: radial-gradient(circle, rgba(15, 118, 110, 0.08) 0%, rgba(15, 118, 110, 0) 74%);
  pointer-events: none;
}
header h1 {
  margin: 0;
  display: inline-flex;
  align-items: center;
  gap: var(--space-4);
  width: fit-content;
  max-width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  box-shadow: none;
  color: #27446d;
  font-size: var(--text-display);
  font-weight: 900;
  line-height: 0.96;
  letter-spacing: -0.06em;
  text-shadow: 0 1px 0 rgba(255, 255, 255, 0.72);
}
.brand-mark {
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  border-radius: 10px;
  filter: drop-shadow(0 4px 10px rgba(15, 118, 110, 0.14));
}
.header-subtitle {
  margin-top: var(--space-1);
  margin-bottom: var(--space-3);
  color: #637796;
  font-size: var(--text-body);
  font-weight: 600;
  letter-spacing: 0.01em;
  line-height: 1.35;
}
.header-meta {
  display: grid;
  gap: var(--space-1);
  margin-top: 0;
  padding-top: var(--space-3);
  border-top: 1px solid rgba(148, 163, 184, 0.28);
  max-width: min(72vw, 980px);
}
.header-meta.hidden {
  display: none;
}
.header-meta-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  flex-wrap: wrap;
  min-width: 0;
}
.header-meta-label {
  flex: 0 0 auto;
  color: #536272;
  font-size: var(--text-label);
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.header-meta-value {
  min-width: 0;
  color: var(--text);
  font-size: var(--text-caption);
  line-height: 1.55;
  overflow-wrap: anywhere;
}
.header-meta-text {
  color: var(--muted);
  font-size: var(--text-label);
  line-height: 1.5;
}
.header-meta-text.error {
  color: #991b1b;
  font-weight: 700;
}
.meta-tag {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  border: 1px solid #d7e1ea;
  background: rgba(255, 255, 255, 0.86);
  color: #334155;
  font-size: var(--text-label);
  font-weight: 700;
}
.meta-tag.source-desktop {
  color: #0f5a5a;
  background: #e5f7f7;
  border-color: #bfe8e8;
}
.meta-tag.source-cli {
  color: #0b3a67;
  background: #e7f1ff;
  border-color: #bdd9f7;
}
.header-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-6);
}
.header-actions {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
  align-self: flex-start;
  padding-top: var(--space-2);
}
.language-select {
  width: auto;
  min-width: 114px;
}
#toggle_session_list_mobile {
  display: none;
}
.container {
  position: relative;
  z-index: 1;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.left {
  position: absolute;
  inset: 0 auto 0 0;
  width: var(--sidebar-width);
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.42);
  border-right: 1px solid var(--line);
  backdrop-filter: blur(16px);
  transition: transform 0.16s ease, opacity 0.12s ease;
  will-change: transform;
}
.right {
  height: 100%;
  margin-left: var(--sidebar-width);
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.36), rgba(248, 251, 255, 0.72));
}
.content-shell,
.events-shell {
  position: relative;
  flex: 1;
  min-height: 0;
}
.section-kicker {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-kicker);
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #0f5a73;
}
.toolbar,
.detail-toolbar {
  display: grid;
  gap: var(--space-5);
  border-bottom: 1px solid var(--line);
}
.toolbar {
  padding: var(--space-5);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.72), rgba(246, 250, 255, 0.94));
  flex: 0 1 auto;
  min-height: 0;
  overflow: auto;
}
.session-count {
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-kicker);
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--muted);
  border-bottom: 1px solid var(--line);
  background: rgba(246, 250, 255, 0.6);
}
.session-count:empty {
  display: none;
}
.match-counter {
  font-size: var(--text-kicker);
  font-weight: 700;
  color: var(--muted);
  white-space: nowrap;
  letter-spacing: 0.04em;
}
.match-counter.hidden {
  display: none;
}
.toolbar-topline,
.detail-toolbar-topline {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-6);
  flex-wrap: wrap;
}
.toolbar-heading,
.detail-toolbar-heading {
  margin-top: var(--space-3);
  font-size: var(--text-title-md);
  line-height: 1.08;
  letter-spacing: -0.03em;
}
.toolbar-copy,
.detail-toolbar-copy {
  margin-top: var(--space-1);
  color: var(--muted);
  font-size: var(--text-caption);
  line-height: 1.55;
}
.toolbar-utility,
.detail-toolbar-utility,
.button-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.toolbar-body,
.detail-toolbar-main {
  display: grid;
  gap: var(--space-4);
}
.detail-toolbar-main {
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
}
.toolbar.collapsed .toolbar-body {
  display: none;
}
.toolbar-section,
.detail-toolbar-row {
  display: grid;
  gap: var(--space-4);
  padding: var(--space-5);
  border: 1px solid var(--line);
  border-radius: 18px;
  background: var(--surface);
  box-shadow: var(--shadow-soft);
}
.detail-toolbar-row.primary,
.detail-toolbar-row.range {
  grid-column: 1 / -1;
}
.detail-toolbar-row.hidden {
  display: none;
}
.toolbar-section-head,
.detail-group-head {
  display: grid;
  gap: var(--space-1);
}
.toolbar-section-title,
.detail-group-title {
  font-size: var(--text-body);
  font-weight: 800;
  color: #0f5a73;
  letter-spacing: 0.03em;
}
.toolbar-section-copy,
.detail-group-copy {
  color: var(--muted);
  font-size: var(--text-caption);
  line-height: 1.45;
}
.field-grid {
  display: grid;
  gap: var(--space-4);
}
.field {
  display: grid;
  gap: var(--space-1);
  min-width: 0;
}
.field > span {
  color: var(--muted);
  font-size: var(--text-kicker);
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.field.inline-field {
  max-width: 260px;
}
.field.field-grow,
.field.field-grow > input {
  width: 100%;
}
.datetime-split {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 112px;
  gap: var(--space-3);
  align-items: center;
}
.datetime-split > input {
  min-width: 0;
}
.datetime-split > input[type="time"] {
  padding-right: var(--space-3);
}
.field-grid .datetime-split,
.detail-event-date-row .datetime-split {
  width: 100%;
}
.datetime-split > input[type="time"]:disabled {
  color: #7b8797;
  border-color: var(--line);
  background: #f3f6f9;
  box-shadow: none;
  cursor: not-allowed;
  opacity: 1;
}
.datetime-split > input[type="date"]:disabled {
  color: #7b8797;
  -webkit-text-fill-color: #7b8797;
  border-color: var(--line);
  background: #f3f6f9;
  box-shadow: none;
  cursor: not-allowed;
  opacity: 1;
}
input,
select,
button {
  font-family: inherit;
  font-size: var(--text-body);
  line-height: 1.4;
}
input:not([type="checkbox"]):not([type="radio"]),
select {
  width: 100%;
  min-height: 38px;
  padding: 0 var(--space-5);
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.96);
  color: var(--text);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.76);
  transition: border-color 0.18s ease, box-shadow 0.18s ease, background-color 0.18s ease;
}
input:not([type="checkbox"]):not([type="radio"])::placeholder {
  color: #95a3b3;
}
input:not([type="checkbox"]):not([type="radio"]):focus,
select:focus {
  outline: none;
  border-color: rgba(15, 118, 110, 0.46);
  box-shadow: 0 0 0 4px rgba(15, 118, 110, 0.1);
}
input[type="checkbox"],
input[type="radio"] {
  width: auto;
  min-height: auto;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  flex: 0 0 auto;
}
button {
  min-height: 34px;
  padding: 0 var(--space-5);
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.96);
  color: #334155;
  cursor: pointer;
  white-space: nowrap;
  font-weight: 700;
  letter-spacing: 0.01em;
  box-shadow: none;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease, background-color 0.18s ease, color 0.18s ease, opacity 0.18s ease;
}
button:hover:not(:disabled):not(.label-remove-button) {
  transform: translateY(-1px);
  border-color: rgba(15, 118, 110, 0.24);
  background: #f8fbff;
}
button:active:not(:disabled):not(.label-remove-button) {
  transform: translateY(0);
}
button:disabled {
  background: #eef3f8;
  color: #98a6b6;
  border-color: #d6e0ea;
  box-shadow: none;
  transform: none;
  cursor: not-allowed;
}
.primary-action,
#reload,
#refresh_detail {
  color: #ffffff;
  border-color: transparent;
  background: linear-gradient(135deg, #0d625b 0%, #084d48 100%);
  box-shadow: 0 6px 14px rgba(8, 77, 72, 0.2);
}
.primary-action:hover:not(:disabled),
#reload:hover:not(:disabled),
#refresh_detail:hover:not(:disabled) {
  background: linear-gradient(135deg, #0f6f68 0%, #095752 100%);
  box-shadow: 0 8px 16px rgba(8, 77, 72, 0.22);
}
.secondary-action,
#clear,
#clear_detail,
#copy_resume_command,
#copy_displayed_messages,
#copy_selected_messages,
#detail_keyword_prev,
#detail_keyword_next,
#detail_keyword_clear,
#clear_message_range_selection,
.event-copy-button {
  background: rgba(255, 255, 255, 0.96);
  color: #334155;
}
#clear_detail_event_date:disabled {
  background: rgba(255, 255, 255, 0.96);
  color: #334155;
  border-color: var(--line-strong);
  opacity: 1;
}
.utility-action,
.secondary-button,
#toggle_filters,
#toggle_detail_actions,
#toggle_session_list_mobile,
#open_label_manager {
  background: transparent;
  color: #536272;
  box-shadow: none;
}
.utility-action:hover:not(:disabled),
.secondary-button:hover:not(:disabled),
#toggle_filters:hover:not(:disabled),
#toggle_detail_actions:hover:not(:disabled),
#toggle_meta:hover:not(:disabled),
#toggle_session_list_mobile:hover:not(:disabled),
#open_label_manager:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.76);
}
#add_session_label,
.event-label-add-button,
#event_selection_mode,
#message_range_selection_mode,
#detail_keyword_filter,
#detail_keyword_search,
#detail_message_range_after,
#detail_message_range_before {
  background: rgba(255, 255, 255, 0.96);
  color: #334155;
}
#event_selection_mode.selection-active,
#message_range_selection_mode.selection-active,
#detail_keyword_filter.active,
#detail_keyword_search.active,
#detail_message_range_after.active {
  color: #ffffff;
  border-color: transparent;
  background: linear-gradient(135deg, var(--accent) 0%, var(--accent-strong) 100%);
  box-shadow: 0 10px 22px rgba(15, 118, 110, 0.18);
}
#detail_message_range_before.active {
  color: #ffffff;
  border-color: transparent;
  background: linear-gradient(135deg, var(--accent) 0%, var(--accent-strong) 100%);
  box-shadow: 0 10px 22px rgba(15, 118, 110, 0.18);
}
#detail_message_range_after.contrast-dim,
#detail_message_range_before.contrast-dim {
  opacity: 0.56;
}
#detail_keyword_q:disabled {
  background: #eef3f8;
  color: #98a6b6;
}
.toggle-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.toggle-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 0 10px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: rgba(248, 251, 255, 0.82);
  color: #334155;
  font-size: var(--text-caption);
  font-weight: 600;
  user-select: none;
}
.toggle-chip.disabled {
  border-color: #d6e0ea;
  background: #eef3f8;
  color: #98a6b6;
}
.toggle-chip input {
  margin: 0;
  accent-color: var(--accent);
}
.sort-tabs {
  display: flex;
  flex: 0 0 auto;
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.68);
}
.sort-tab {
  flex: 1;
  padding: 7px 4px;
  border: none;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--muted);
  font-size: var(--text-kicker);
  font-weight: 700;
  letter-spacing: 0.03em;
  cursor: pointer;
  transition: color 0.15s ease, border-color 0.15s ease, background-color 0.15s ease;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sort-tab:hover {
  color: var(--text);
  background: rgba(15, 118, 110, 0.04);
}
.sort-tab.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
  background: rgba(15, 118, 110, 0.06);
}
#sessions {
  height: 100%;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 10px;
  display: grid;
  gap: 8px;
  min-width: 0;
  background: linear-gradient(180deg, rgba(248, 251, 255, 0.66), rgba(239, 246, 253, 0.94));
}
.session-item {
  padding: 10px 12px;
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.88);
  cursor: pointer;
  transition: transform 0.18s ease, border-color 0.18s ease, background-color 0.18s ease, box-shadow 0.18s ease;
}
.session-item:hover {
  transform: translateY(-1px);
  border-color: rgba(15, 118, 110, 0.2);
  background: rgba(255, 255, 255, 0.96);
  box-shadow: var(--shadow-soft);
}
.session-item.active {
  border-color: rgba(15, 118, 110, 0.45);
  border-left: 3px solid var(--accent);
  background: linear-gradient(180deg, rgba(15, 118, 110, 0.14), rgba(15, 118, 110, 0.06));
  box-shadow: var(--shadow-medium);
}
.session-path {
  font-size: var(--text-caption);
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-cwd {
  color: #0b5f3d;
  font-weight: 700;
  background: #e9f7ef;
  border: 1px solid #bfe8cf;
  border-radius: 999px;
  padding: 2px 8px;
  display: inline-flex;
  align-items: center;
  min-width: 0;
  max-width: 100%;
}
.session-time {
  color: #7a4b00;
  font-weight: 700;
  background: #fff4df;
  border: 1px solid #f2d4a5;
  border-radius: 999px;
  padding: 2px 8px;
  display: inline-block;
  max-width: 100%;
  font-variant-numeric: tabular-nums;
}
.session-path .ts,
.header-meta-value .ts {
  color: #0b4a52;
  font-weight: 700;
  background: #dff5f8;
  border-radius: 999px;
  padding: 0 6px;
}
.session-preview {
  margin-top: 8px;
  font-size: var(--text-caption);
  line-height: 1.5;
  color: #354252;
}
.session-meta-row,
.session-label-row {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}
.session-meta-row-secondary {
  margin-top: 0;
}
.session-badge {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  min-width: 0;
  padding: 0 8px;
  border-radius: 999px;
  border: 1px solid #d7e1ea;
  background: #f3f8ff;
  font-size: var(--text-label);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-label-row:empty {
  display: none;
}
.session-meta-row-secondary .session-cwd {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1 1 auto;
}
.session-source {
  font-weight: 700;
}
.session-source.source-desktop,
.meta-tag.source-desktop {
  color: #0f5a5a;
  background: #e5f7f7;
  border-color: #bfe8e8;
}
.session-source.source-cli,
.meta-tag.source-cli {
  color: #0b3a67;
  background: #e7f1ff;
  border-color: #bdd9f7;
}
.session-tag-subagents {
  color: #6b21a8;
  background: #f3e8ff;
  border-color: #d8b4fe;
  font-weight: 700;
}
.detail-toolbar {
  gap: 0;
  padding: 4px 16px 6px;
  background: linear-gradient(180deg, rgba(247, 250, 255, 0.95), rgba(242, 247, 253, 0.88));
}
.detail-toolbar-copy,
.detail-toolbar .section-kicker,
.detail-group-copy {
  display: none;
}
.detail-toolbar-topline {
  display: none;
}
.detail-toolbar-main {
  grid-template-columns: 1fr;
  gap: 0;
}
.detail-toolbar-row {
  grid-template-columns: 72px minmax(0, 1fr);
  align-items: start;
  gap: 6px 12px;
  padding: 8px 0;
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}
.detail-toolbar-row.primary {
  grid-template-columns: 72px minmax(0, 1fr) auto;
}
.detail-toolbar-row:first-child {
  border-top: 0;
}
.detail-group-head {
  gap: 2px;
  padding-top: 3px;
}
.detail-toolbar-row.primary .toggle-list {
  grid-column: 2;
}
.detail-group-title {
  font-size: 11px;
  color: #536272;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.detail-toolbar-row.primary .field.inline-field {
  grid-column: 2;
  min-width: 120px;
  max-width: 132px;
}
.detail-toolbar-row.primary .detail-toolbar-utility {
  grid-column: 3;
  grid-row: 1 / span 2;
  align-self: start;
  justify-content: flex-end;
  padding-top: 1px;
}
.detail-toolbar-row.primary .field > span {
  display: none;
}
.detail-toolbar-row.primary #detail_event_label_filter {
  min-width: 120px;
  padding-right: 28px;
}
.detail-toolbar-row.keyword .button-row,
.detail-toolbar-row.range .button-row {
  grid-column: 2;
}
.detail-event-date-row {
  grid-column: 2;
  display: flex;
  align-items: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}
.detail-event-date-row .field {
  flex: 0 1 240px;
  max-width: 240px;
}
.detail-event-date-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.session-label-strip {
  min-height: 40px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--line);
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  background: rgba(255, 255, 255, 0.72);
}
.session-label-strip.empty {
  color: var(--muted);
  font-size: var(--text-body);
}
#events {
  height: 100%;
  overflow: auto;
  padding: 14px 16px;
}
.status-wrap {
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.status-layer {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(248, 251, 255, 0.72);
  backdrop-filter: blur(4px);
  z-index: 5;
}
.status-layer.hidden {
  display: none;
}
.status-card {
  width: min(100%, 380px);
  display: grid;
  gap: 12px;
  justify-items: center;
  text-align: center;
  padding: 16px 18px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: var(--shadow-medium);
}
.status-card.empty {
  border-style: dashed;
  box-shadow: none;
}
.status-card.error {
  border-color: #fecaca;
  background: rgba(255, 245, 245, 0.98);
}
.status-title {
  color: #0f172a;
  font-size: var(--text-body);
  font-weight: 700;
}
.status-copy {
  color: var(--muted);
  font-size: var(--text-caption);
  line-height: 1.7;
}
.status-spinner,
.status-icon {
  width: 30px;
  height: 30px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}
.status-spinner {
  border: 3px solid #d6e3f2;
  border-top-color: var(--accent);
  animation: status-spin 0.9s linear infinite;
}
.status-icon {
  background: #e2e8f0;
  color: #475569;
  font-size: 14px;
  font-weight: 800;
}
.status-icon.error {
  background: #fee2e2;
  color: #b91c1c;
}
@keyframes status-spin {
  to {
    transform: rotate(360deg);
  }
}
.ev {
  --event-accent: var(--line-strong);
  --event-tint: rgba(255, 255, 255, 0.76);
  margin-bottom: 10px;
  padding: 12px 14px;
  border: 1px solid rgba(203, 213, 225, 0.84);
  border-left: 4px solid var(--event-accent);
  border-radius: 14px;
  background: linear-gradient(180deg, var(--event-tint), rgba(255, 255, 255, 0.97) 72%);
  box-shadow: none;
}
.ev.user {
  --event-accent: var(--user);
  --event-tint: rgba(37, 99, 235, 0.05);
}
.ev.user_context {
  --event-accent: #94a3b8;
  --event-tint: rgba(148, 163, 184, 0.08);
}
.ev.assistant {
  --event-accent: var(--assistant);
  --event-tint: rgba(15, 118, 110, 0.06);
}
.ev.developer {
  --event-accent: var(--dev);
  --event-tint: rgba(180, 83, 9, 0.07);
}
.ev.system {
  --event-accent: var(--system);
  --event-tint: rgba(100, 116, 139, 0.06);
}
.ev.label-match {
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.08);
}
.ev.copy-selected {
  outline: 2px solid rgba(37, 99, 235, 0.18);
  outline-offset: 2px;
}
.ev.range-anchor-selected {
  box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.1);
}
.detail-keyword-hit {
  background: #fde68a;
  color: inherit;
  padding: 0 2px;
  border-radius: 4px;
}
.detail-keyword-hit.current {
  background: #f59e0b;
  color: #1f2937;
}
.ev-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 8px;
  color: var(--muted);
  font-size: var(--text-caption);
}
.event-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-left: auto;
}
.event-select-toggle,
.event-range-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: var(--text-label);
  font-weight: 700;
  background: rgba(255, 255, 255, 0.82);
}
.event-select-toggle {
  border: 1px solid rgba(37, 99, 235, 0.2);
  color: #1e3a8a;
}
.event-range-toggle {
  border: 1px solid rgba(15, 118, 110, 0.18);
  color: #0f5a73;
}
.event-select-toggle input,
.event-range-toggle input {
  margin: 0;
}
.event-select-toggle input {
  accent-color: #2563eb;
}
.event-range-toggle input {
  accent-color: var(--accent);
}
.event-label-add-button,
.event-copy-button {
  min-height: 28px;
  padding: 0 8px;
  font-size: var(--text-caption);
}
.badge-kind,
.badge-role,
.badge-time {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  border: 1px solid transparent;
  font-weight: 700;
}
.badge-kind {
  color: #334155;
  background: #eef3f8;
  border-color: #d7e1ea;
}
.badge-time {
  color: #556474;
  background: #f7f9fc;
  border-color: #dde6ef;
  font-variant-numeric: tabular-nums;
}
.badge-system-label {
  color: #6b21a8;
  background: #f3e8ff;
  border-color: #d8b4fe;
}
.badge-role.user {
  color: #0f4fbe;
  background: #dbeafe;
  border-color: #bfd7ff;
}
.badge-role.user_context {
  color: #475569;
  background: #eef2f7;
  border-color: #d7e1ea;
}
.badge-role.assistant {
  color: #0b6a41;
  background: #dcf6e8;
  border-color: #b5e3cb;
}
.badge-role.developer {
  color: #7a4b00;
  background: #ffedd5;
  border-color: #f5d1a1;
}
.badge-role.system {
  color: #44505d;
  background: #eef2f7;
  border-color: #d7e1ea;
}
.data-label-badge {
  --label-color: #94a3b8;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid var(--label-color);
  background: rgba(255, 255, 255, 0.94);
  color: #1f2937;
  font-size: var(--text-label);
  font-weight: 700;
}
.data-label-badge .label-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--label-color);
  flex: 0 0 auto;
}
.data-label-badge .label-remove-button {
  border: 0;
  min-height: auto;
  padding: 0;
  background: transparent;
  color: #64748b;
  line-height: 1;
  font-size: 12px;
  box-shadow: none;
}
.data-label-badge .label-remove-button:hover {
  color: #0f172a;
  background: transparent;
}
.label-picker {
  position: fixed;
  z-index: 9999;
  min-width: 220px;
  max-width: 280px;
  display: grid;
  gap: 6px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: var(--shadow-medium);
  backdrop-filter: blur(14px);
}
.label-picker.hidden {
  display: none;
}
.label-picker-option {
  width: 100%;
  justify-content: flex-start;
  display: flex;
  align-items: center;
  gap: 8px;
}
.label-picker-option .label-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--label-color);
  flex: 0 0 auto;
}
.label-picker-empty {
  padding: 6px 8px;
  color: var(--muted);
  font-size: var(--text-caption);
}
.shortcut-dialog {
  position: fixed;
  inset: 0;
  z-index: 9998;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(15, 23, 42, 0.28);
  backdrop-filter: blur(8px);
}
.shortcut-dialog.hidden {
  display: none;
}
.shortcut-card {
  width: min(520px, 100%);
  max-height: min(78vh, 760px);
  display: grid;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: var(--shadow-medium);
  overflow: auto;
}
.shortcut-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.shortcut-title {
  font-size: var(--text-title-sm);
  font-weight: 800;
  letter-spacing: -0.02em;
}
.shortcut-copy {
  color: var(--muted);
  font-size: var(--text-caption);
  line-height: 1.5;
}
.shortcut-list {
  display: grid;
  gap: 8px;
}
.shortcut-row {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(248, 251, 255, 0.82);
}
.shortcut-keys {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-wrap: nowrap;
  white-space: nowrap;
}
.shortcut-keys kbd {
  min-width: 28px;
  min-height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 8px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: #ffffff;
  color: #334155;
  font: 700 var(--text-label)/1 var(--font-mono);
}
.shortcut-plus {
  color: #64748b;
  font-size: var(--text-caption);
  font-weight: 700;
  line-height: 1;
}
.shortcut-desc {
  color: #334155;
  font-size: var(--text-caption);
  line-height: 1.45;
}
pre {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid rgba(203, 213, 225, 0.56);
  border-radius: 12px;
  background: rgba(248, 250, 252, 0.78);
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  font-size: var(--text-body);
  line-height: 1.65;
  font-family: var(--font-mono);
}
@media (max-width: 1180px) {
  :root {
    --sidebar-width: 304px;
  }
  .detail-toolbar-row.primary {
    grid-template-columns: 72px minmax(0, 1fr);
  }
  .detail-toolbar-row.primary .detail-toolbar-utility {
    grid-column: 2;
    grid-row: auto;
    justify-content: flex-start;
    padding-top: 0;
  }
}
@media (max-width: 1080px) {
  .detail-toolbar-main {
    grid-template-columns: 1fr;
  }
  .detail-toolbar-row.primary,
  .detail-toolbar-row.secondary,
  .detail-toolbar-row.keyword,
  .detail-toolbar-row.range {
    grid-column: auto;
  }
}
@media (max-width: 900px) {
  .header-bar {
    flex-direction: column;
    align-items: stretch;
  }
  .header-actions {
    align-self: stretch;
    justify-content: flex-start;
    padding-top: 0;
  }
  #toggle_session_list_mobile {
    display: inline-flex;
  }
  .container {
    display: grid;
    grid-template-columns: 1fr;
    grid-template-rows: 42vh 1fr;
  }
  .container.sidebar-collapsed {
    grid-template-rows: 0 1fr;
  }
  .left,
  .right {
    position: static;
    inset: auto;
    width: auto;
    margin-left: 0;
    transition: none;
    will-change: auto;
  }
  .left {
    grid-column: 1;
    grid-row: 1;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }
  .right {
    grid-column: 1;
    grid-row: 2;
    height: auto;
  }
  .container.sidebar-collapsed .left {
    opacity: 0;
    pointer-events: none;
  }
  .toolbar,
  .detail-toolbar,
  .session-label-strip,
  #events {
    padding-left: 14px;
    padding-right: 14px;
  }
}
@media (max-width: 640px) {
  header {
    padding: 10px 14px;
  }
  .header-main {
    padding: 10px 14px 8px;
    border-radius: 18px;
  }
  header h1 {
    font-size: var(--text-display-compact);
  }
  .header-subtitle {
    font-size: var(--text-body);
  }
  .toolbar,
  .detail-toolbar {
    gap: 10px;
  }
  .toolbar-section,
  .detail-toolbar-row {
    padding: 8px 0;
    border-radius: 0;
  }
  #sessions,
  #events {
    padding: 10px;
  }
  .event-actions {
    margin-left: 0;
  }
  .header-meta {
    max-width: none;
  }
  .detail-toolbar-row {
    grid-template-columns: 1fr;
    gap: 6px;
  }
  .detail-toolbar-row.primary .field.inline-field,
  .detail-toolbar-row.keyword .button-row,
  .detail-toolbar-row.range .button-row,
  .detail-event-date-row {
    grid-column: auto;
    max-width: none;
  }
  .detail-event-date-row .field {
    max-width: none;
    flex: 1 1 100%;
  }
}
</style>
</head>
<body>
<header>
  <div class="header-bar">
    <div class="header-main">
      <h1><img class="brand-mark" src="/icons/claude-sessions-viewer.svg" alt="" aria-hidden="true" />Claude Sessions Viewer</h1>
      <div class="header-subtitle">Session logs, labels, and event review for Claude workflows</div>
      <div id="meta" class="header-meta hidden" aria-live="polite"></div>
    </div>
    <div class="header-actions">
      <select id="language_select" class="language-select" aria-label="Language">
        <option value="ja">日本語</option>
        <option value="en">English</option>
        <option value="zh-Hans">简体中文</option>
        <option value="zh-Hant">繁體中文</option>
      </select>
      <button id="open_label_manager" class="utility-action">ラベル管理</button>
      <button id="toggle_meta" class="utility-action" title="M">メタ表示</button>
      <button id="open_shortcuts" class="utility-action" title="ショートカット一覧を表示">ショートカット</button>
      <button id="toggle_session_list_mobile" class="utility-action">一覧を隠す</button>
    </div>
  </div>
</header>
  <div class="container">
  <aside class="left">
    <div class="toolbar">
      <div class="toolbar-topline">
        <div>
          <div class="section-kicker">Session Browser</div>
          <div class="toolbar-heading">検索と絞り込み</div>
          <div class="toolbar-copy">候補を探してから一覧を見る、という流れに整理しました。</div>
        </div>
        <div class="toolbar-utility">
          <button id="reload" class="primary-action" title="F5">Reload</button>
          <button id="clear" class="secondary-action" title="Shift + L">Clear</button>
          <button id="toggle_filters" class="utility-action" title="Shift + F">フィルタを隠す</button>
        </div>
      </div>
      <div class="toolbar-body">
        <section class="toolbar-section">
          <div class="toolbar-section-head">
            <div class="toolbar-section-title">検索</div>
            <div class="toolbar-section-copy">cwd とキーワードで候補を先に絞り込みます。</div>
          </div>
          <div class="field-grid">
            <label class="field">
              <span>作業ディレクトリ</span>
              <input id="cwd_q" placeholder="cwd (部分一致)" />
            </label>
            <label class="field">
              <span>キーワード</span>
              <input id="q" placeholder="keyword filter" />
            </label>
            <label class="field">
              <span>条件</span>
              <select id="mode">
                <option value="and">keyword AND</option>
                <option value="or">keyword OR</option>
              </select>
            </label>
          </div>
        </section>
        <section class="toolbar-section">
          <div class="toolbar-section-head">
            <div class="toolbar-section-title">フィルター</div>
            <div class="toolbar-section-copy">期間・source・ラベルで一覧を整理します。</div>
          </div>
          <div class="field-grid">
            <label class="field">
              <span>開始日</span>
              <input id="date_from" type="date" />
            </label>
            <label class="field">
              <span>終了日</span>
              <input id="date_to" type="date" />
            </label>
            <div class="field">
              <span id="event_date_from_label">イベント開始日時</span>
              <div class="datetime-split">
                <input id="event_date_from_date" type="date" aria-label="イベント開始日時 日付" />
                <input id="event_date_from_time" type="time" step="60" aria-label="イベント開始日時 時間" />
              </div>
            </div>
            <div class="field">
              <span id="event_date_to_label">イベント終了日時</span>
              <div class="datetime-split">
                <input id="event_date_to_date" type="date" aria-label="イベント終了日時 日付" />
                <input id="event_date_to_time" type="time" step="60" aria-label="イベント終了日時 時間" />
              </div>
            </div>
            <label class="field">
              <span>source</span>
              <select id="source_filter">
                <option value="all">source: all</option>
                <option value="claude_cli">source: Claude Code CLI</option>
                <option value="claude_desktop">source: Claude Desktop</option>
              </select>
            </label>
            <label class="field">
              <span>subagents</span>
              <select id="subagents_filter">
                <option value="include">含む</option>
                <option value="exclude">含まない</option>
              </select>
            </label>
            <label class="field">
              <span>セッションラベル</span>
              <select id="session_label_filter">
                <option value="">session label: all</option>
              </select>
            </label>
            <label class="field">
              <span>イベントラベル</span>
              <select id="event_label_filter">
                <option value="">event label: all</option>
              </select>
            </label>
          </div>
        </section>
      </div>
    </div>
    <div id="session_count" class="session-count" aria-live="polite"></div>
    <div class="sort-tabs" role="tablist">
      <button class="sort-tab active" data-sort="desc" role="tab" aria-selected="true">新しい順</button>
      <button class="sort-tab" data-sort="asc" role="tab" aria-selected="false">古い順</button>
      <button class="sort-tab" data-sort="updated" role="tab" aria-selected="false">最終更新日時順</button>
    </div>
    <div class="content-shell">
      <div id="sessions"></div>
      <div id="sessions_status" class="status-layer hidden" aria-live="polite"></div>
    </div>
  </aside>
  <main class="right">
    <div class="detail-toolbar">
      <div class="detail-toolbar-main">
        <section class="detail-toolbar-row primary">
          <div class="detail-group-head">
            <div class="detail-group-title">表示</div>
            <div class="detail-group-copy">今見たいログだけに整えます。</div>
          </div>
          <div class="toggle-list">
            <label class="toggle-chip" title="1"><input type="checkbox" id="only_user_instruction" /> ユーザー指示のみ表示</label>
            <label class="toggle-chip" title="2"><input type="checkbox" id="only_ai_response" /> AIレスポンスのみ表示</label>
            <label class="toggle-chip" title="3"><input type="checkbox" id="turn_boundary_only" /> 各入力と最終応答のみ</label>
            <label class="toggle-chip" title="4"><input type="checkbox" id="reverse_order" /> 表示順を逆にする</label>
          </div>
          <label class="field inline-field">
            <span>イベントラベル</span>
            <select id="detail_event_label_filter" title="ラベルで絞り込み">
              <option value="">label</option>
            </select>
          </label>
          <div class="detail-toolbar-utility">
            <button id="refresh_detail" class="primary-action" title="F5" disabled>Refresh</button>
            <button id="clear_detail" class="secondary-action" title="Shift + D" disabled>Clear</button>
            <button id="toggle_detail_actions" class="utility-action" title="Shift + T">詳細操作を隠す</button>
          </div>
        </section>
        <section id="detail_action_row" class="detail-toolbar-row secondary">
          <div class="detail-group-head">
            <div class="detail-group-title">操作</div>
            <div class="detail-group-copy">コピー、ラベル付け、選択モードをここにまとめます。</div>
          </div>
          <div class="button-row">
            <button id="copy_resume_command" class="secondary-action" title="Shift + R" disabled>セッション再開コマンドコピー</button>
            <button id="add_session_label" disabled>セッションにラベル追加</button>
            <button id="copy_displayed_messages" class="secondary-action" title="Shift + C" disabled>表示中メッセージコピー</button>
            <button id="event_selection_mode" title="Shift + S" disabled>選択モード</button>
            <button id="copy_selected_messages" class="secondary-action" title="Shift + X" disabled>選択コピー</button>
          </div>
        </section>
        <section id="detail_keyword_row" class="detail-toolbar-row keyword">
          <div class="detail-group-head">
            <div class="detail-group-title">検索</div>
            <div class="detail-group-copy">詳細キーワードのフィルターとヒット移動を分離しました。</div>
          </div>
          <label class="field field-grow">
            <span>詳細キーワード</span>
            <input id="detail_keyword_q" placeholder="detail keyword" title="/ でフォーカス" />
          </label>
          <div class="button-row">
            <button id="detail_keyword_filter" disabled>フィルター</button>
            <button id="detail_keyword_search" disabled>検索</button>
            <button id="detail_keyword_prev" class="secondary-action" title="P" disabled>前へ</button>
            <button id="detail_keyword_next" class="secondary-action" title="N" disabled>次へ</button>
            <span id="detail_keyword_match_count" class="match-counter hidden"></span>
            <button id="detail_keyword_clear" class="secondary-action" disabled>検索をクリア</button>
          </div>
          <div class="detail-event-date-row">
            <div class="field">
              <span id="detail_event_date_from_label">イベント開始日時</span>
              <div class="datetime-split">
                <input id="detail_event_date_from_date" type="date" aria-label="詳細イベント開始日時 日付" />
                <input id="detail_event_date_from_time" type="time" step="60" aria-label="詳細イベント開始日時 時間" />
              </div>
            </div>
            <div class="field">
              <span id="detail_event_date_to_label">イベント終了日時</span>
              <div class="datetime-split">
                <input id="detail_event_date_to_date" type="date" aria-label="詳細イベント終了日時 日付" />
                <input id="detail_event_date_to_time" type="time" step="60" aria-label="詳細イベント終了日時 時間" />
              </div>
            </div>
            <div class="detail-event-date-actions">
              <button id="clear_detail_event_date" class="secondary-action">日時クリア</button>
            </div>
          </div>
        </section>
        <section id="detail_message_range_row" class="detail-toolbar-row range">
          <div class="detail-group-head">
            <div class="detail-group-title">範囲選択</div>
            <div class="detail-group-copy">起点を決めて、前後どちらを見るかを明確に切り替えます。</div>
          </div>
          <div class="button-row">
            <button id="message_range_selection_mode" title="Shift + G" disabled>起点選択モード</button>
            <button id="clear_message_range_selection" class="secondary-action" title="Shift + H" disabled>起点解除</button>
            <button id="detail_message_range_after" title="." disabled>起点以降のみ表示</button>
            <button id="detail_message_range_before" title="," disabled>起点以前のみ表示</button>
          </div>
        </section>
      </div>
    </div>
    <div class="session-label-strip empty" id="session_label_strip">セッションラベルはまだありません</div>
    <div class="events-shell">
      <div id="events"></div>
      <div id="detail_status" class="status-layer hidden" aria-live="polite"></div>
    </div>
  </main>
</div>
<div id="label_picker" class="label-picker hidden"></div>
<div id="shortcut_dialog" class="shortcut-dialog hidden" role="dialog" aria-modal="true" aria-labelledby="shortcut_dialog_title">
  <div class="shortcut-card">
    <div class="shortcut-head">
      <div>
        <div id="shortcut_dialog_title" class="shortcut-title">ショートカット</div>
        <div class="shortcut-copy">入力欄にカーソルがある間は実行されません。`Esc` で閉じるか、検索入力からカーソルを外せます。</div>
      </div>
      <button id="close_shortcuts" class="utility-action" type="button">閉じる</button>
    </div>
    <div class="shortcut-list">
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>F5</kbd></div>
        <div class="shortcut-desc">表示中の一覧またはセッション詳細を更新</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>Shift</kbd><span class="shortcut-plus">+</span><kbd>F</kbd></div>
        <div class="shortcut-desc">左ペインのフィルタ表示を切り替え</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>Shift</kbd><span class="shortcut-plus">+</span><kbd>L</kbd></div>
        <div class="shortcut-desc">左ペインの Clear を実行</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>/</kbd></div>
        <div class="shortcut-desc">検索入力欄にフォーカス</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>N</kbd></div>
        <div class="shortcut-desc">詳細検索の次のヒットへ移動</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>P</kbd></div>
        <div class="shortcut-desc">詳細検索の前のヒットへ移動</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>M</kbd></div>
        <div class="shortcut-desc">path / cwd / time のメタ表示を切り替え</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>[</kbd></div>
        <div class="shortcut-desc">前のセッションを開く</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>]</kbd></div>
        <div class="shortcut-desc">次のセッションを開く</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>1</kbd></div>
        <div class="shortcut-desc">ユーザー指示のみ表示を切り替え</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>2</kbd></div>
        <div class="shortcut-desc">AIレスポンスのみ表示を切り替え</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>3</kbd></div>
        <div class="shortcut-desc">各入力と最終応答のみを切り替え</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>4</kbd></div>
        <div class="shortcut-desc">表示順を逆にするを切り替え</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>Shift</kbd><span class="shortcut-plus">+</span><kbd>D</kbd></div>
        <div class="shortcut-desc">右ペインの Clear を実行</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>Shift</kbd><span class="shortcut-plus">+</span><kbd>T</kbd></div>
        <div class="shortcut-desc">詳細操作の表示と非表示を切り替え</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>Shift</kbd><span class="shortcut-plus">+</span><kbd>R</kbd></div>
        <div class="shortcut-desc">セッション再開コマンドをコピー</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>Shift</kbd><span class="shortcut-plus">+</span><kbd>C</kbd></div>
        <div class="shortcut-desc">表示中メッセージをコピー</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>Shift</kbd><span class="shortcut-plus">+</span><kbd>S</kbd></div>
        <div class="shortcut-desc">選択モードの開始と終了を切り替え</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>Shift</kbd><span class="shortcut-plus">+</span><kbd>X</kbd></div>
        <div class="shortcut-desc">選択中メッセージをコピー</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>Shift</kbd><span class="shortcut-plus">+</span><kbd>G</kbd></div>
        <div class="shortcut-desc">起点選択モードの開始と終了を切り替え</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>Shift</kbd><span class="shortcut-plus">+</span><kbd>H</kbd></div>
        <div class="shortcut-desc">起点を解除</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>,</kbd></div>
        <div class="shortcut-desc">起点以前のみ表示</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>.</kbd></div>
        <div class="shortcut-desc">起点以降のみ表示</div>
      </div>
      <div class="shortcut-row">
        <div class="shortcut-keys"><kbd>Esc</kbd></div>
        <div class="shortcut-desc">ショートカット一覧やラベル追加ポップアップを閉じる。検索入力欄からカーソルを外す。</div>
      </div>
    </div>
  </div>
</div>
<script>
const state = {
  sessions: [],
  filtered: [],
  activePath: null,
  activeSession: null,
  activeEvents: [],
  activeRawLineCount: 0,
  sessionRoot: '',
  labels: [],
  isSessionsLoading: false,
  hasLoadedSessions: false,
  sessionsError: '',
  sessionsLoadMode: '',
  isDetailLoading: false,
  detailError: '',
  detailLoadMode: '',
  isEventSelectionMode: false,
  selectedEventIds: new Set(),
  isMessageRangeSelectionMode: false,
  selectedMessageRangeEventId: '',
  detailMessageRangeMode: '',
};

const FILTER_STORAGE_KEY = 'claude_sessions_viewer_filters_v4';
const LANGUAGE_STORAGE_KEY = 'claude_sessions_viewer_language_v1';
const SUPPORTED_LANGUAGES = ['ja', 'en', 'zh-Hans', 'zh-Hant'];
const I18N = {
  ja: {
    'language.selector': '言語',
    'header.subtitle': 'Session logs, labels, and event review for Claude workflows',
    'header.shortcuts': 'ショートカット',
    'header.meta.show': 'メタ表示',
    'header.meta.hide': 'メタ非表示',
    'header.list.hide': 'セッション一覧を隠す',
    'header.list.show': 'セッション一覧を表示',
    'header.list.hideShort': '一覧を隠す',
    'header.list.showShort': '一覧を表示',
    'header.labels': 'ラベル管理',
    'toolbar.kicker': 'Session Browser',
    'toolbar.heading': '検索と絞り込み',
    'toolbar.copy': '候補を探してから一覧を見る、という流れに整理しました。',
    'toolbar.reload': 'Reload',
    'toolbar.clear': 'Clear',
    'toolbar.filters.hide': 'フィルタを隠す',
    'toolbar.filters.show': 'フィルタを表示',
    'search.title': '検索',
    'search.copy': 'cwd とキーワードで候補を先に絞り込みます。',
    'search.cwd': '作業ディレクトリ',
    'search.keyword': 'キーワード',
    'search.mode': '条件',
    'filter.title': 'フィルター',
    'filter.copy': '期間・source・ラベルで一覧を整理します。',
    'filter.dateFrom': '開始日',
    'filter.dateTo': '終了日',
    'filter.eventDateFrom': 'イベント開始日時',
    'filter.eventDateTo': 'イベント終了日時',
    'common.date': '日付',
    'common.time': '時間',
    'filter.source': 'source',
    'filter.subagents': 'subagents',
    'filter.sessionLabel': 'セッションラベル',
    'filter.eventLabel': 'イベントラベル',
    'filter.source.all': 'source: all',
    'filter.source.cli': 'source: Claude Code CLI',
    'filter.source.vscode': 'source: Claude Desktop',
    'filter.subagents.include': '含む',
    'filter.subagents.exclude': '含まない',
    'filter.sessionLabel.all': 'session label: all',
    'filter.eventLabel.all': 'event label: all',
    'filter.sort': '並び順',
    'filter.sort.desc': '新しい順',
    'filter.sort.asc': '古い順',
    'filter.sort.updated': '最終更新日時順',
    'filter.mode.and': 'keyword AND',
    'filter.mode.or': 'keyword OR',
    'placeholder.cwd': 'cwd (部分一致)',
    'placeholder.keyword': 'keyword filter',
    'placeholder.detailKeyword': 'detail keyword',
    'detail.display': '表示',
    'detail.toggle.user': 'ユーザー指示のみ表示',
    'detail.toggle.ai': 'AIレスポンスのみ表示',
    'detail.toggle.turn': '各入力と最終応答のみ',
    'detail.toggle.reverse': '表示順を逆にする',
    'detail.label': 'イベントラベル',
    'detail.label.all': 'label',
    'detail.refresh': 'Refresh',
    'detail.refreshing': 'Refreshing...',
    'detail.clear': 'Clear',
    'detail.actions.hide': '詳細操作を隠す',
    'detail.actions.show': '詳細操作を表示',
    'detail.actions': '操作',
    'detail.copyResume': 'セッション再開コマンドコピー',
    'detail.addSessionLabel': 'セッションにラベル追加',
    'detail.copyDisplayed': '表示中メッセージコピー',
    'detail.selectMode': '選択モード',
    'detail.selectEnd': '選択終了',
    'detail.copySelected': '選択コピー',
    'detail.copySelectedCount': '選択コピー ({count}件)',
    'detail.search': '検索',
    'detail.searchKeyword': '詳細キーワード',
    'detail.searchFilter': 'フィルター',
    'detail.searchFilterClear': 'フィルター解除',
    'detail.searchRun': '検索',
    'detail.prev': '前へ',
    'detail.next': '次へ',
    'detail.matchCounter': '{current} / {total}',
    'detail.searchClear': '検索をクリア',
    'detail.eventDateFrom': 'イベント開始日時',
    'detail.eventDateTo': 'イベント終了日時',
    'detail.eventDateClear': '日時クリア',
    'detail.range': '範囲選択',
    'detail.rangeMode': '起点選択モード',
    'detail.rangeModeEnd': '起点選択終了',
    'detail.rangeClear': '起点解除',
    'detail.rangeAfter': '起点以降のみ表示',
    'detail.rangeAfterActive': '起点以降のみ表示中',
    'detail.rangeBefore': '起点以前のみ表示',
    'detail.rangeBeforeActive': '起点以前のみ表示中',
    'session.labels.empty': 'セッションラベルはまだありません',
    'session.labels.loading': 'セッションラベルを読み込み中...',
    'shortcut.title': 'ショートカット',
    'shortcut.copy': '入力欄にカーソルがある間は実行されません。Esc で閉じるか、検索入力からカーソルを外せます。',
    'shortcut.close': '閉じる',
    'shortcut.refresh': '表示中の一覧またはセッション詳細を更新',
    'shortcut.toggleFilters': '左ペインのフィルタ表示を切り替え',
    'shortcut.clearList': '左ペインの Clear を実行',
    'shortcut.focusSearch': '検索入力欄にフォーカス',
    'shortcut.nextMatch': '詳細検索の次のヒットへ移動',
    'shortcut.prevMatch': '詳細検索の前のヒットへ移動',
    'shortcut.meta': 'path / cwd / time のメタ表示を切り替え',
    'shortcut.prevSession': '前のセッションを開く',
    'shortcut.nextSession': '次のセッションを開く',
    'shortcut.onlyUser': 'ユーザー指示のみ表示を切り替え',
    'shortcut.onlyAi': 'AIレスポンスのみ表示を切り替え',
    'shortcut.turnBoundary': '各入力と最終応答のみを切り替え',
    'shortcut.reverse': '表示順を逆にするを切り替え',
    'shortcut.clearDetail': '右ペインの表示条件と操作状態をクリア',
    'shortcut.toggleActions': '詳細操作の表示と非表示を切り替え',
    'shortcut.copyResume': 'セッション再開コマンドをコピー',
    'shortcut.copyDisplayed': '表示中メッセージをコピー',
    'shortcut.toggleSelection': '選択モードの開始と終了を切り替え',
    'shortcut.copySelected': '選択中メッセージをコピー',
    'shortcut.toggleRange': '起点選択モードの開始と終了を切り替え',
    'shortcut.clearRange': '起点を解除',
    'shortcut.before': '起点以前のみ表示',
    'shortcut.after': '起点以降のみ表示',
    'shortcut.escape': 'ショートカット一覧やラベル追加ポップアップを閉じる。検索入力欄からカーソルを外す。',
    'meta.sessionRoot': 'session root',
    'meta.path': 'path',
    'meta.cwd': 'cwd',
    'meta.time': 'time',
    'meta.status': 'status',
    'summary.sessions': 'sessions: {current} / {filtered} / {total}',
    'summary.events': 'events: {visible}/{total}',
    'summary.eventsLoading': 'events: loading...',
    'summary.raw': 'raw {count}',
    'session.preview.empty': '(previewなし)',
    'status.sessions.loadingTitle': 'セッション一覧を読み込み中...',
    'status.sessions.loadingCopy': '最新のセッションを確認しています。',
    'status.sessions.errorTitle': '一覧の取得に失敗しました',
    'status.sessions.noMatchesTitle': '条件に一致するセッションはありません',
    'status.sessions.noMatchesCopy': 'フィルタ条件を見直すか、Reload を実行してください。',
    'status.sessions.emptyTitle': 'セッションがまだ見つかりません',
    'status.sessions.emptyCopy': '読み込み対象ディレクトリに .jsonl セッションがあるか確認してください。',
    'status.sessions.refreshTitle': '一覧を更新中...',
    'status.sessions.refreshCopy': '最新のセッションを再取得しています。',
    'status.detail.loadingTitle': 'セッション詳細を読み込み中...',
    'status.detail.loadingCopy': 'イベントを取得しています。',
    'status.detail.errorTitle': '詳細の取得に失敗しました',
    'status.detail.selectSession': 'セッションを選択してください',
    'status.detail.noDisplayTitle': '表示できるイベントはありません',
    'status.detail.noDisplayCopy': 'このセッションには表示対象のイベントがありません。',
    'status.detail.noMatchTitle': '条件に一致するイベントはありません',
    'status.detail.noMatchCopy': '表示条件を変更するとイベントが表示される可能性があります。',
    'status.detail.refreshTitle': 'セッション詳細を更新中...',
    'status.detail.refreshCopy': '最新のイベントを再取得しています。',
    'error.sessions': 'セッション一覧の取得に失敗しました',
    'error.detail': 'セッション詳細の取得に失敗しました',
    'picker.noLabels': 'ラベルがありません。先にラベル管理から作成してください。',
    'picker.removeLabel': 'ラベル解除',
    'picker.addLabel': 'ラベル追加',
    'copy.copied': 'コピーしました',
    'copy.displayedCount': '{count}件コピー',
    'copy.selectedCount': '{count}件コピー',
    'copy.single': 'コピー',
  },
  en: {
    'language.selector': 'Language',
    'header.subtitle': 'Session logs, labels, and event review for Claude workflows',
    'header.shortcuts': 'Shortcuts',
    'header.meta.show': 'Show meta',
    'header.meta.hide': 'Hide meta',
    'header.list.hide': 'Hide session list',
    'header.list.show': 'Show session list',
    'header.list.hideShort': 'Hide list',
    'header.list.showShort': 'Show list',
    'header.labels': 'Labels',
    'toolbar.kicker': 'Session Browser',
    'toolbar.heading': 'Search and filter',
    'toolbar.copy': 'Find candidates first, then review the list.',
    'toolbar.reload': 'Reload',
    'toolbar.clear': 'Clear',
    'toolbar.filters.hide': 'Hide filters',
    'toolbar.filters.show': 'Show filters',
    'search.title': 'Search',
    'search.copy': 'Narrow candidates with cwd and keywords first.',
    'search.cwd': 'Working directory',
    'search.keyword': 'Keyword',
    'search.mode': 'Mode',
    'filter.title': 'Filters',
    'filter.copy': 'Organize the list by time range, source, and labels.',
    'filter.dateFrom': 'Start date',
    'filter.dateTo': 'End date',
    'filter.eventDateFrom': 'Event start date/time',
    'filter.eventDateTo': 'Event end date/time',
    'common.date': 'Date',
    'common.time': 'Time',
    'filter.source': 'Source',
    'filter.subagents': 'subagents',
    'filter.sessionLabel': 'Session label',
    'filter.eventLabel': 'Event label',
    'filter.source.all': 'source: all',
    'filter.source.cli': 'source: Claude Code CLI',
    'filter.source.vscode': 'source: Claude Desktop',
    'filter.subagents.include': 'Include',
    'filter.subagents.exclude': 'Exclude',
    'filter.sessionLabel.all': 'session label: all',
    'filter.eventLabel.all': 'event label: all',
    'filter.sort': 'Sort order',
    'filter.sort.desc': 'Newest first',
    'filter.sort.asc': 'Oldest first',
    'filter.sort.updated': 'Last updated',
    'filter.mode.and': 'keyword AND',
    'filter.mode.or': 'keyword OR',
    'placeholder.cwd': 'cwd (partial match)',
    'placeholder.keyword': 'keyword filter',
    'placeholder.detailKeyword': 'detail keyword',
    'detail.display': 'Display',
    'detail.toggle.user': 'Only user instructions',
    'detail.toggle.ai': 'Only AI responses',
    'detail.toggle.turn': 'Only each input and final reply',
    'detail.toggle.reverse': 'Reverse order',
    'detail.label': 'Event label',
    'detail.label.all': 'label',
    'detail.refresh': 'Refresh',
    'detail.refreshing': 'Refreshing...',
    'detail.clear': 'Clear',
    'detail.actions.hide': 'Hide detail actions',
    'detail.actions.show': 'Show detail actions',
    'detail.actions': 'Actions',
    'detail.copyResume': 'Copy resume command',
    'detail.addSessionLabel': 'Add session label',
    'detail.copyDisplayed': 'Copy displayed messages',
    'detail.selectMode': 'Selection mode',
    'detail.selectEnd': 'End selection',
    'detail.copySelected': 'Copy selected',
    'detail.copySelectedCount': 'Copy selected ({count})',
    'detail.search': 'Search',
    'detail.searchKeyword': 'Detail keyword',
    'detail.searchFilter': 'Filter',
    'detail.searchFilterClear': 'Clear filter',
    'detail.searchRun': 'Search',
    'detail.prev': 'Prev',
    'detail.next': 'Next',
    'detail.matchCounter': '{current} / {total}',
    'detail.searchClear': 'Clear search',
    'detail.eventDateFrom': 'Event start date/time',
    'detail.eventDateTo': 'Event end date/time',
    'detail.eventDateClear': 'Clear dates',
    'detail.range': 'Range',
    'detail.rangeMode': 'Anchor mode',
    'detail.rangeModeEnd': 'End anchor mode',
    'detail.rangeClear': 'Clear anchor',
    'detail.rangeAfter': 'Show from anchor',
    'detail.rangeAfterActive': 'Showing from anchor',
    'detail.rangeBefore': 'Show until anchor',
    'detail.rangeBeforeActive': 'Showing until anchor',
    'session.labels.empty': 'No session labels yet',
    'session.labels.loading': 'Loading session labels...',
    'shortcut.title': 'Shortcuts',
    'shortcut.copy': 'Shortcuts do not run while an input is focused. Press Esc to close or leave search fields.',
    'shortcut.close': 'Close',
    'shortcut.refresh': 'Refresh the current list or session detail',
    'shortcut.toggleFilters': 'Toggle the left-pane filters',
    'shortcut.clearList': 'Run Clear on the left pane',
    'shortcut.focusSearch': 'Focus the search input',
    'shortcut.nextMatch': 'Move to the next detail-search match',
    'shortcut.prevMatch': 'Move to the previous detail-search match',
    'shortcut.meta': 'Toggle meta details for path / cwd / time',
    'shortcut.prevSession': 'Open the previous session',
    'shortcut.nextSession': 'Open the next session',
    'shortcut.onlyUser': 'Toggle only user instructions',
    'shortcut.onlyAi': 'Toggle only AI responses',
    'shortcut.turnBoundary': 'Toggle only each input and final reply',
    'shortcut.reverse': 'Toggle reverse order',
    'shortcut.clearDetail': 'Clear right-pane filters and active modes',
    'shortcut.toggleActions': 'Toggle detail actions',
    'shortcut.copyResume': 'Copy the session resume command',
    'shortcut.copyDisplayed': 'Copy displayed messages',
    'shortcut.toggleSelection': 'Toggle selection mode',
    'shortcut.copySelected': 'Copy selected messages',
    'shortcut.toggleRange': 'Toggle anchor mode',
    'shortcut.clearRange': 'Clear the anchor',
    'shortcut.before': 'Show only before the anchor',
    'shortcut.after': 'Show only after the anchor',
    'shortcut.escape': 'Close the shortcut list or label picker, and leave search fields.',
    'meta.sessionRoot': 'session root',
    'meta.path': 'path',
    'meta.cwd': 'cwd',
    'meta.time': 'time',
    'meta.status': 'status',
    'summary.sessions': 'sessions: {current} / {filtered} / {total}',
    'summary.events': 'events: {visible}/{total}',
    'summary.eventsLoading': 'events: loading...',
    'summary.raw': 'raw {count}',
    'session.preview.empty': '(no preview)',
    'status.sessions.loadingTitle': 'Loading sessions...',
    'status.sessions.loadingCopy': 'Checking the latest sessions.',
    'status.sessions.errorTitle': 'Failed to load the list',
    'status.sessions.noMatchesTitle': 'No sessions match these filters',
    'status.sessions.noMatchesCopy': 'Review the filters or run Reload.',
    'status.sessions.emptyTitle': 'No sessions found yet',
    'status.sessions.emptyCopy': 'Check whether the target directory contains .jsonl sessions.',
    'status.sessions.refreshTitle': 'Refreshing list...',
    'status.sessions.refreshCopy': 'Fetching the latest sessions again.',
    'status.detail.loadingTitle': 'Loading session detail...',
    'status.detail.loadingCopy': 'Fetching events.',
    'status.detail.errorTitle': 'Failed to load detail',
    'status.detail.selectSession': 'Select a session',
    'status.detail.noDisplayTitle': 'No events to display',
    'status.detail.noDisplayCopy': 'This session has no displayable events.',
    'status.detail.noMatchTitle': 'No events match these conditions',
    'status.detail.noMatchCopy': 'Changing the display conditions may reveal events.',
    'status.detail.refreshTitle': 'Refreshing session detail...',
    'status.detail.refreshCopy': 'Fetching the latest events again.',
    'error.sessions': 'Failed to load the session list',
    'error.detail': 'Failed to load the session detail',
    'picker.noLabels': 'No labels exist yet. Create one in Label Manager first.',
    'picker.removeLabel': 'Remove label',
    'picker.addLabel': 'Add label',
    'copy.copied': 'Copied',
    'copy.displayedCount': 'Copied {count}',
    'copy.selectedCount': 'Copied {count}',
    'copy.single': 'Copy',
  },
  'zh-Hans': {
    'language.selector': '语言',
    'header.subtitle': '用于 Claude 工作流的会话日志、标签和事件查看',
    'header.shortcuts': '快捷键',
    'header.meta.show': '显示元信息',
    'header.meta.hide': '隐藏元信息',
    'header.list.hide': '隐藏会话列表',
    'header.list.show': '显示会话列表',
    'header.list.hideShort': '隐藏列表',
    'header.list.showShort': '显示列表',
    'header.labels': '标签管理',
    'toolbar.kicker': 'Session Browser',
    'toolbar.heading': '搜索与筛选',
    'toolbar.copy': '先找到候选项，再查看列表。',
    'toolbar.reload': 'Reload',
    'toolbar.clear': 'Clear',
    'toolbar.filters.hide': '隐藏筛选',
    'toolbar.filters.show': '显示筛选',
    'search.title': '搜索',
    'search.copy': '先用 cwd 和关键词缩小候选范围。',
    'search.cwd': '工作目录',
    'search.keyword': '关键词',
    'search.mode': '模式',
    'filter.title': '筛选',
    'filter.copy': '按时间范围、source 和标签整理列表。',
    'filter.dateFrom': '开始日期',
    'filter.dateTo': '结束日期',
    'filter.eventDateFrom': '事件开始日期/时间',
    'filter.eventDateTo': '事件结束日期/时间',
    'common.date': '日期',
    'common.time': '时间',
    'filter.source': '来源',
    'filter.subagents': 'subagents',
    'filter.sessionLabel': '会话标签',
    'filter.eventLabel': '事件标签',
    'filter.source.all': 'source: all',
    'filter.source.cli': 'source: Claude Code CLI',
    'filter.source.vscode': 'source: Claude Desktop',
    'filter.subagents.include': '包含',
    'filter.subagents.exclude': '不包含',
    'filter.sessionLabel.all': 'session label: all',
    'filter.eventLabel.all': 'event label: all',
    'filter.sort': '排序',
    'filter.sort.desc': '最新优先',
    'filter.sort.asc': '最旧优先',
    'filter.sort.updated': '最后更新时间',
    'filter.mode.and': 'keyword AND',
    'filter.mode.or': 'keyword OR',
    'placeholder.cwd': 'cwd（部分匹配）',
    'placeholder.keyword': '关键词筛选',
    'placeholder.detailKeyword': '详细关键词',
    'detail.display': '显示',
    'detail.toggle.user': '仅显示用户指令',
    'detail.toggle.ai': '仅显示 AI 回复',
    'detail.toggle.turn': '仅显示每次输入与最终回复',
    'detail.toggle.reverse': '反转显示顺序',
    'detail.label': '事件标签',
    'detail.label.all': 'label',
    'detail.refresh': 'Refresh',
    'detail.refreshing': 'Refreshing...',
    'detail.clear': 'Clear',
    'detail.actions.hide': '隐藏详细操作',
    'detail.actions.show': '显示详细操作',
    'detail.actions': '操作',
    'detail.copyResume': '复制恢复命令',
    'detail.addSessionLabel': '为会话添加标签',
    'detail.copyDisplayed': '复制当前显示消息',
    'detail.selectMode': '选择模式',
    'detail.selectEnd': '结束选择',
    'detail.copySelected': '复制已选',
    'detail.copySelectedCount': '复制已选（{count}）',
    'detail.search': '搜索',
    'detail.searchKeyword': '详细关键词',
    'detail.searchFilter': '筛选',
    'detail.searchFilterClear': '清除过滤',
    'detail.searchRun': '搜索',
    'detail.prev': '上一项',
    'detail.next': '下一项',
    'detail.matchCounter': '{current} / {total}',
    'detail.searchClear': '清除搜索',
    'detail.eventDateFrom': '事件开始日期/时间',
    'detail.eventDateTo': '事件结束日期/时间',
    'detail.eventDateClear': '清除日期',
    'detail.range': '范围',
    'detail.rangeMode': '锚点模式',
    'detail.rangeModeEnd': '结束锚点模式',
    'detail.rangeClear': '清除锚点',
    'detail.rangeAfter': '仅显示锚点之后',
    'detail.rangeAfterActive': '正在显示锚点之后',
    'detail.rangeBefore': '仅显示锚点之前',
    'detail.rangeBeforeActive': '正在显示锚点之前',
    'session.labels.empty': '还没有会话标签',
    'session.labels.loading': '正在加载会话标签...',
    'shortcut.title': '快捷键',
    'shortcut.copy': '输入框获得焦点时不会触发快捷键。按 Esc 可关闭，或离开搜索输入框。',
    'shortcut.close': '关闭',
    'shortcut.refresh': '刷新当前列表或会话详情',
    'shortcut.toggleFilters': '切换左侧筛选显示',
    'shortcut.clearList': '执行左侧 Clear',
    'shortcut.focusSearch': '聚焦到搜索输入框',
    'shortcut.nextMatch': '跳到详细搜索的下一个命中',
    'shortcut.prevMatch': '跳到详细搜索的上一个命中',
    'shortcut.meta': '切换 path / cwd / time 元信息显示',
    'shortcut.prevSession': '打开上一个会话',
    'shortcut.nextSession': '打开下一个会话',
    'shortcut.onlyUser': '切换仅显示用户指令',
    'shortcut.onlyAi': '切换仅显示 AI 回复',
    'shortcut.turnBoundary': '切换仅显示每次输入与最终回复',
    'shortcut.reverse': '切换反转显示顺序',
    'shortcut.clearDetail': '清除右侧筛选与当前模式',
    'shortcut.toggleActions': '切换详细操作显示',
    'shortcut.copyResume': '复制会话恢复命令',
    'shortcut.copyDisplayed': '复制当前显示消息',
    'shortcut.toggleSelection': '切换选择模式',
    'shortcut.copySelected': '复制已选消息',
    'shortcut.toggleRange': '切换锚点模式',
    'shortcut.clearRange': '清除锚点',
    'shortcut.before': '仅显示锚点之前',
    'shortcut.after': '仅显示锚点之后',
    'shortcut.escape': '关闭快捷键列表或标签选择框，并离开搜索输入框。',
    'meta.sessionRoot': 'session root',
    'meta.path': 'path',
    'meta.cwd': 'cwd',
    'meta.time': 'time',
    'meta.status': 'status',
    'summary.sessions': 'sessions: {current} / {filtered} / {total}',
    'summary.events': 'events: {visible}/{total}',
    'summary.eventsLoading': 'events: loading...',
    'summary.raw': 'raw {count}',
    'session.preview.empty': '(无预览)',
    'status.sessions.loadingTitle': '正在加载会话列表...',
    'status.sessions.loadingCopy': '正在检查最新会话。',
    'status.sessions.errorTitle': '加载列表失败',
    'status.sessions.noMatchesTitle': '没有符合筛选条件的会话',
    'status.sessions.noMatchesCopy': '请检查筛选条件或执行 Reload。',
    'status.sessions.emptyTitle': '尚未找到会话',
    'status.sessions.emptyCopy': '请确认目标目录中是否存在 .jsonl 会话文件。',
    'status.sessions.refreshTitle': '正在刷新列表...',
    'status.sessions.refreshCopy': '正在重新获取最新会话。',
    'status.detail.loadingTitle': '正在加载会话详情...',
    'status.detail.loadingCopy': '正在获取事件。',
    'status.detail.errorTitle': '加载详情失败',
    'status.detail.selectSession': '请选择一个会话',
    'status.detail.noDisplayTitle': '没有可显示的事件',
    'status.detail.noDisplayCopy': '此会话中没有可显示的事件。',
    'status.detail.noMatchTitle': '没有符合条件的事件',
    'status.detail.noMatchCopy': '调整显示条件后可能会出现事件。',
    'status.detail.refreshTitle': '正在刷新会话详情...',
    'status.detail.refreshCopy': '正在重新获取最新事件。',
    'error.sessions': '获取会话列表失败',
    'error.detail': '获取会话详情失败',
    'picker.noLabels': '还没有标签。请先在标签管理中创建标签。',
    'picker.removeLabel': '移除标签',
    'picker.addLabel': '添加标签',
    'copy.copied': '已复制',
    'copy.displayedCount': '已复制 {count} 项',
    'copy.selectedCount': '已复制 {count} 项',
    'copy.single': '复制',
  },
};
I18N['zh-Hant'] = {
  ...I18N['zh-Hans'],
  'language.selector': '語言',
  'header.subtitle': '用於 Claude 工作流程的工作階段日誌、標籤與事件檢視',
  'header.meta.show': '顯示中繼資訊',
  'header.meta.hide': '隱藏中繼資訊',
  'header.list.hide': '隱藏工作階段列表',
  'header.list.show': '顯示工作階段列表',
  'header.list.hideShort': '隱藏列表',
  'header.list.showShort': '顯示列表',
  'header.labels': '標籤管理',
  'toolbar.heading': '搜尋與篩選',
  'toolbar.copy': '先找到候選項，再查看列表。',
  'toolbar.filters.hide': '隱藏篩選',
  'toolbar.filters.show': '顯示篩選',
  'search.title': '搜尋',
  'search.copy': '先用 cwd 和關鍵字縮小候選範圍。',
  'search.cwd': '工作目錄',
  'search.keyword': '關鍵字',
  'filter.sessionLabel': '工作階段標籤',
  'filter.title': '篩選',
  'filter.copy': '按時間範圍、source 和標籤整理列表。',
  'filter.dateFrom': '開始日期',
  'filter.dateTo': '結束日期',
  'filter.eventDateFrom': '事件開始日期/時間',
  'filter.eventDateTo': '事件結束日期/時間',
  'common.date': '日期',
  'common.time': '時間',
  'filter.source': '來源',
  'filter.subagents': 'subagents',
  'filter.eventLabel': '事件標籤',
  'filter.subagents.include': '包含',
  'filter.subagents.exclude': '不包含',
  'filter.sort': '排序',
  'filter.sort.desc': '最新優先',
  'filter.sort.asc': '最舊優先',
  'filter.sort.updated': '最後更新時間',
  'placeholder.cwd': 'cwd（部分比對）',
  'placeholder.keyword': '關鍵字篩選',
  'placeholder.detailKeyword': '詳細關鍵字',
  'detail.display': '顯示',
  'detail.toggle.user': '僅顯示使用者指示',
  'detail.toggle.ai': '僅顯示 AI 回覆',
  'detail.toggle.turn': '僅顯示每次輸入與最終回覆',
  'detail.toggle.reverse': '反轉顯示順序',
  'detail.label': '事件標籤',
  'detail.refresh': '刷新',
  'detail.refreshing': '正在刷新...',
  'detail.clear': '清除',
  'detail.actions.hide': '隱藏詳細操作',
  'detail.actions.show': '顯示詳細操作',
  'detail.actions': '操作',
  'detail.copyResume': '複製恢復命令',
  'detail.addSessionLabel': '為工作階段新增標籤',
  'detail.copyDisplayed': '複製目前顯示訊息',
  'detail.selectMode': '選取模式',
  'detail.selectEnd': '結束選取',
  'detail.copySelected': '複製已選',
  'detail.copySelectedCount': '複製已選（{count}）',
  'detail.search': '搜尋',
  'detail.searchKeyword': '詳細關鍵字',
  'detail.searchFilterClear': '清除篩選',
  'detail.searchRun': '搜尋',
  'detail.searchFilter': '篩選',
  'detail.prev': '上一項',
  'detail.next': '下一項',
  'detail.matchCounter': '{current} / {total}',
  'detail.searchClear': '清除搜尋',
  'detail.eventDateFrom': '事件開始日期/時間',
  'detail.eventDateTo': '事件結束日期/時間',
  'detail.eventDateClear': '清除日期',
  'detail.range': '範圍',
  'detail.rangeMode': '錨點模式',
  'detail.rangeModeEnd': '結束錨點模式',
  'detail.rangeClear': '清除錨點',
  'detail.rangeAfter': '僅顯示錨點之後',
  'detail.rangeAfterActive': '正在顯示錨點之後',
  'detail.rangeBefore': '僅顯示錨點之前',
  'detail.rangeBeforeActive': '正在顯示錨點之前',
  'session.labels.empty': '尚未有工作階段標籤',
  'session.labels.loading': '正在載入工作階段標籤...',
  'shortcut.title': '快捷鍵',
  'shortcut.copy': '輸入框取得焦點時不會觸發快捷鍵。按 Esc 可關閉，或離開搜尋輸入框。',
  'shortcut.close': '關閉',
  'shortcut.refresh': '重新整理目前列表或工作階段詳情',
  'shortcut.toggleFilters': '切換左側篩選顯示',
  'shortcut.clearList': '執行左側 Clear',
  'shortcut.focusSearch': '將焦點移到搜尋輸入框',
  'shortcut.nextMatch': '跳到詳細搜尋的下一個命中',
  'shortcut.prevMatch': '跳到詳細搜尋的上一個命中',
  'shortcut.meta': '切換 path / cwd / time 中繼資訊顯示',
  'shortcut.prevSession': '開啟上一個工作階段',
  'shortcut.nextSession': '開啟下一個工作階段',
  'shortcut.onlyUser': '切換僅顯示使用者指示',
  'shortcut.onlyAi': '切換僅顯示 AI 回覆',
  'shortcut.turnBoundary': '切換僅顯示每次輸入與最終回覆',
  'shortcut.reverse': '切換反轉顯示順序',
  'shortcut.clearDetail': '清除右側篩選與目前模式',
  'shortcut.toggleActions': '切換詳細操作顯示',
  'shortcut.copyResume': '複製工作階段恢復命令',
  'shortcut.copyDisplayed': '複製目前顯示訊息',
  'shortcut.toggleSelection': '切換選取模式',
  'shortcut.copySelected': '複製已選訊息',
  'shortcut.toggleRange': '切換錨點模式',
  'shortcut.clearRange': '清除錨點',
  'shortcut.before': '僅顯示錨點之前',
  'shortcut.after': '僅顯示錨點之後',
  'shortcut.escape': '關閉快捷鍵列表或標籤選擇框，並離開搜尋輸入框。',
  'summary.sessions': 'sessions: {filtered}/{total}',
  'session.preview.empty': '(無預覽)',
  'status.sessions.loadingTitle': '正在載入工作階段列表...',
  'status.sessions.loadingCopy': '正在檢查最新工作階段。',
  'status.sessions.errorTitle': '載入列表失敗',
  'status.sessions.noMatchesTitle': '沒有符合篩選條件的工作階段',
  'status.sessions.noMatchesCopy': '請檢查篩選條件或執行 Reload。',
  'status.sessions.emptyTitle': '尚未找到工作階段',
  'status.sessions.emptyCopy': '請確認目標目錄中是否存在 .jsonl 工作階段檔案。',
  'status.sessions.refreshTitle': '正在刷新列表...',
  'status.sessions.refreshCopy': '正在重新取得最新工作階段。',
  'status.detail.loadingTitle': '正在載入工作階段詳情...',
  'status.detail.loadingCopy': '正在取得事件。',
  'status.detail.errorTitle': '載入詳情失敗',
  'status.detail.selectSession': '請選擇一個工作階段',
  'status.detail.noDisplayTitle': '沒有可顯示的事件',
  'status.detail.noDisplayCopy': '此工作階段中沒有可顯示的事件。',
  'status.detail.noMatchTitle': '沒有符合條件的事件',
  'status.detail.noMatchCopy': '調整顯示條件後可能會出現事件。',
  'status.detail.refreshTitle': '正在刷新工作階段詳情...',
  'status.detail.refreshCopy': '正在重新取得最新事件。',
  'error.sessions': '取得工作階段列表失敗',
  'error.detail': '取得工作階段詳情失敗',
  'picker.noLabels': '尚未有標籤。請先在標籤管理中建立標籤。',
  'picker.removeLabel': '移除標籤',
  'picker.addLabel': '新增標籤',
  'copy.copied': '已複製',
  'copy.displayedCount': '已複製 {count} 項',
  'copy.selectedCount': '已複製 {count} 項',
  'copy.single': '複製',
};
let uiLanguage = 'ja';

function normalizeLanguage(value){
  const raw = (value || '').trim();
  if(raw === 'zh' || raw === 'zh-CN' || raw === 'zh-SG'){
    return 'zh-Hans';
  }
  if(raw === 'zh-TW' || raw === 'zh-HK' || raw === 'zh-MO'){
    return 'zh-Hant';
  }
  return SUPPORTED_LANGUAGES.includes(raw) ? raw : 'ja';
}

function t(key, vars){
  const dict = I18N[uiLanguage] || I18N.ja;
  let text = dict[key];
  if(typeof text !== 'string'){
    text = I18N.ja[key] || key;
  }
  if(vars){
    Object.entries(vars).forEach(([name, value]) => {
      text = text.replaceAll(`{${name}}`, String(value));
    });
  }
  return text;
}

function setText(selector, value){
  const element = document.querySelector(selector);
  if(element){
    element.textContent = value;
  }
}

function setTextById(id, value){
  const element = document.getElementById(id);
  if(element){
    element.textContent = value;
  }
}

function setFieldLabel(inputId, value){
  const input = document.getElementById(inputId);
  const label = input ? input.closest('label') : null;
  const span = label ? label.querySelector('span') : null;
  if(span){
    span.textContent = value;
  }
}

function setInputAriaLabel(id, value){
  const input = document.getElementById(id);
  if(input){
    input.setAttribute('aria-label', value);
  }
}

function setDateTimePairAria(dateId, timeId, label){
  setInputAriaLabel(dateId, `${label} ${t('common.date')}`);
  setInputAriaLabel(timeId, `${label} ${t('common.time')}`);
}

function setToggleLabel(inputId, value){
  const input = document.getElementById(inputId);
  const label = input ? input.closest('label') : null;
  if(!label){
    return;
  }
  const textNode = Array.from(label.childNodes).find(node => node.nodeType === Node.TEXT_NODE);
  if(textNode){
    textNode.textContent = ` ${value}`;
  }
}

function setOptionText(selectId, index, value){
  const select = document.getElementById(selectId);
  if(select && select.options[index]){
    select.options[index].textContent = value;
  }
}

function applyMainLanguage(){
  document.documentElement.lang = uiLanguage;
  document.title = 'Claude Sessions Viewer';
  document.getElementById('language_select').value = uiLanguage;
  document.getElementById('language_select').setAttribute('aria-label', t('language.selector'));
  setText('.header-subtitle', t('header.subtitle'));
  setTextById('open_shortcuts', t('header.shortcuts'));
  document.getElementById('open_shortcuts').setAttribute('title', t('header.shortcuts'));
  setTextById('open_label_manager', t('header.labels'));
  setText('.toolbar .section-kicker', t('toolbar.kicker'));
  setText('.toolbar .toolbar-heading', t('toolbar.heading'));
  setText('.toolbar .toolbar-copy', t('toolbar.copy'));
  setTextById('reload', t('toolbar.reload'));
  setTextById('clear', t('toolbar.clear'));
  setText('.toolbar-section:nth-of-type(1) .toolbar-section-title', t('search.title'));
  setText('.toolbar-section:nth-of-type(1) .toolbar-section-copy', t('search.copy'));
  setFieldLabel('cwd_q', t('search.cwd'));
  setFieldLabel('q', t('search.keyword'));
  setFieldLabel('mode', t('search.mode'));
  setText('.toolbar-section:nth-of-type(2) .toolbar-section-title', t('filter.title'));
  setText('.toolbar-section:nth-of-type(2) .toolbar-section-copy', t('filter.copy'));
  setFieldLabel('date_from', t('filter.dateFrom'));
  setFieldLabel('date_to', t('filter.dateTo'));
  setTextById('event_date_from_label', t('filter.eventDateFrom'));
  setTextById('event_date_to_label', t('filter.eventDateTo'));
  setInputAriaLabel('date_from', t('filter.dateFrom'));
  setInputAriaLabel('date_to', t('filter.dateTo'));
  setDateTimePairAria('event_date_from_date', 'event_date_from_time', t('filter.eventDateFrom'));
  setDateTimePairAria('event_date_to_date', 'event_date_to_time', t('filter.eventDateTo'));
  setFieldLabel('source_filter', t('filter.source'));
  setFieldLabel('subagents_filter', t('filter.subagents'));
  setFieldLabel('session_label_filter', t('filter.sessionLabel'));
  setFieldLabel('event_label_filter', t('filter.eventLabel'));
  document.querySelectorAll('.sort-tab').forEach(tab => {
    const key = 'filter.sort.' + tab.dataset.sort;
    tab.textContent = t(key);
  });
  document.getElementById('cwd_q').placeholder = t('placeholder.cwd');
  document.getElementById('q').placeholder = t('placeholder.keyword');
  document.getElementById('detail_keyword_q').placeholder = t('placeholder.detailKeyword');
  setOptionText('mode', 0, t('filter.mode.and'));
  setOptionText('mode', 1, t('filter.mode.or'));
  setOptionText('source_filter', 0, t('filter.source.all'));
  setOptionText('source_filter', 1, t('filter.source.cli'));
  setOptionText('source_filter', 2, t('filter.source.vscode'));
  setOptionText('subagents_filter', 0, t('filter.subagents.include'));
  setOptionText('subagents_filter', 1, t('filter.subagents.exclude'));
  setText('.detail-toolbar-row.primary .detail-group-title', t('detail.display'));
  setToggleLabel('only_user_instruction', t('detail.toggle.user'));
  setToggleLabel('only_ai_response', t('detail.toggle.ai'));
  setToggleLabel('turn_boundary_only', t('detail.toggle.turn'));
  document.getElementById('turn_boundary_only').closest('label').setAttribute('title', '3');
  setToggleLabel('reverse_order', t('detail.toggle.reverse'));
  setFieldLabel('detail_event_label_filter', t('detail.label'));
  document.getElementById('detail_event_label_filter').setAttribute('title', t('detail.label'));
  setTextById('clear_detail', t('detail.clear'));
  setText('.detail-toolbar-row.secondary .detail-group-title', t('detail.actions'));
  setTextById('copy_resume_command', t('detail.copyResume'));
  setTextById('add_session_label', t('detail.addSessionLabel'));
  setTextById('copy_displayed_messages', t('detail.copyDisplayed'));
  setText('.detail-toolbar-row.keyword .detail-group-title', t('detail.search'));
  setFieldLabel('detail_keyword_q', t('detail.searchKeyword'));
  document.getElementById('detail_keyword_q').setAttribute('title', '/');
  setTextById('detail_keyword_filter', t('detail.searchFilter'));
  setTextById('detail_keyword_search', t('detail.searchRun'));
  setTextById('detail_keyword_prev', t('detail.prev'));
  setTextById('detail_keyword_next', t('detail.next'));
  setTextById('detail_keyword_clear', t('detail.searchClear'));
  setTextById('detail_event_date_from_label', t('detail.eventDateFrom'));
  setTextById('detail_event_date_to_label', t('detail.eventDateTo'));
  setDateTimePairAria('detail_event_date_from_date', 'detail_event_date_from_time', t('detail.eventDateFrom'));
  setDateTimePairAria('detail_event_date_to_date', 'detail_event_date_to_time', t('detail.eventDateTo'));
  setTextById('clear_detail_event_date', t('detail.eventDateClear'));
  setText('.detail-toolbar-row.range .detail-group-title', t('detail.range'));
  setTextById('clear_message_range_selection', t('detail.rangeClear'));
  const shortcutDescriptions = [
    'shortcut.refresh',
    'shortcut.toggleFilters',
    'shortcut.clearList',
    'shortcut.focusSearch',
    'shortcut.nextMatch',
    'shortcut.prevMatch',
    'shortcut.meta',
    'shortcut.prevSession',
    'shortcut.nextSession',
    'shortcut.onlyUser',
    'shortcut.onlyAi',
    'shortcut.turnBoundary',
    'shortcut.reverse',
    'shortcut.clearDetail',
    'shortcut.toggleActions',
    'shortcut.copyResume',
    'shortcut.copyDisplayed',
    'shortcut.toggleSelection',
    'shortcut.copySelected',
    'shortcut.toggleRange',
    'shortcut.clearRange',
    'shortcut.before',
    'shortcut.after',
    'shortcut.escape',
  ];
  document.querySelectorAll('.shortcut-desc').forEach((element, index) => {
    const key = shortcutDescriptions[index];
    if(key){
      element.textContent = t(key);
    }
  });
  setTextById('shortcut_dialog_title', t('shortcut.title'));
  setText('.shortcut-copy', t('shortcut.copy'));
  setTextById('close_shortcuts', t('shortcut.close'));
  populateLabelControls();
  refreshDateTimeInputPairStates();
  updateFilterVisibility();
  updateDetailActionsVisibility();
  updateDetailMetaVisibility();
  updateLeftPaneVisibility();
  updateRefreshDetailButtonState();
  updateDetailDisplayControlsState();
  updateClearDetailButtonState();
  updateCopyResumeButtonState();
  updateDisplayedMessagesCopyButtonState();
  updateEventSelectionModeButtonState();
  updateCopySelectedMessagesButtonState();
  updateMessageRangeSelectionModeButtonState();
  updateClearMessageRangeSelectionButtonState();
  updateMessageRangeFilterButtonsState();
  renderSessionList();
  renderSessionLabelStrip();
  renderActiveSession();
}

function setUiLanguage(nextLanguage, persist){
  const normalized = normalizeLanguage(nextLanguage);
  uiLanguage = normalized;
  if(persist !== false){
    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, normalized);
    } catch (e) {
      // Ignore storage write errors.
    }
  }
  applyMainLanguage();
}

function readStoredLanguage(){
  try {
    return localStorage.getItem(LANGUAGE_STORAGE_KEY) || '';
  } catch (e) {
    return '';
  }
}

function getRequestedLanguage(){
  const params = new URLSearchParams(window.location.search);
  return normalizeLanguage(params.get('lang') || readStoredLanguage() || uiLanguage);
}

const SEARCH_DEBOUNCE_MS = 180;
const BUTTON_FEEDBACK_MS = 1200;
const DETAIL_INTERACTION_LOCK_MS = 4000;
let loadSessionsTimer = null;
let loadSessionsRequestSeq = 0;
let loadSessionDetailRequestSeq = 0;
let saveFiltersFrame = 0;
let deferredDetailSyncTimer = 0;
let labelManagerWindow = null;
let labelPickerHandler = null;
let datePickers = [];
let dateTimePickers = [];
let filtersVisible = true;
let detailActionsVisible = true;
let detailMetaVisible = false;
let leftPaneVisible = true;
let pendingAutomaticDetailSync = false;
let detailPointerDown = false;
let detailInteractionLockUntil = 0;
let detailKeywordFilterTerm = '';
let detailKeywordSearchTerm = '';
let detailKeywordCurrentMatchIndex = -1;
let pendingDetailKeywordFocusIndex = -1;
let detailKeywordSearchTotal = 0;

function esc(s){
  return (s ?? '').toString().replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
}

function renderColorStyle(colorValue){
  return `--label-color:${esc(colorValue || '#94a3b8')}`;
}

function buildStatusCard(title, copy, tone){
  const kind = tone || 'loading';
  const indicator = kind === 'loading'
    ? '<span class="status-spinner" aria-hidden="true"></span>'
    : `<span class="status-icon ${kind === 'error' ? 'error' : ''}" aria-hidden="true">${kind === 'error' ? '!' : 'i'}</span>`;
  return `<div class="status-card ${esc(kind)}">${indicator}<div class="status-title">${esc(title || '')}</div>${copy ? `<div class="status-copy">${esc(copy)}</div>` : ''}</div>`;
}

function renderInlineStatus(title, copy, tone){
  return `<div class="status-wrap">${buildStatusCard(title, copy, tone)}</div>`;
}

function setStatusLayer(id, title, copy, tone){
  const layer = document.getElementById(id);
  if(!layer){
    return;
  }
  if(!title){
    layer.classList.add('hidden');
    layer.innerHTML = '';
    return;
  }
  layer.innerHTML = buildStatusCard(title, copy, tone);
  layer.classList.remove('hidden');
}

function updateReloadButtonState(){
  const button = document.getElementById('reload');
  if(!button){
    return;
  }
  const isManualReload = state.isSessionsLoading && state.sessionsLoadMode === 'reload';
  button.disabled = isManualReload;
  button.textContent = isManualReload ? 'Reloading...' : 'Reload';
}

function updateFilterVisibility(){
  const toolbar = document.querySelector('.toolbar');
  const button = document.getElementById('toggle_filters');
  if(filtersVisible){
    toolbar.classList.remove('collapsed');
    button.textContent = t('toolbar.filters.hide');
  } else {
    toolbar.classList.add('collapsed');
    button.textContent = t('toolbar.filters.show');
  }
}

function setFiltersVisible(nextVisible){
  filtersVisible = !!nextVisible;
  updateFilterVisibility();
  saveFiltersSoon();
}

function updateDetailActionsVisibility(){
  const actionRow = document.getElementById('detail_action_row');
  const keywordRow = document.getElementById('detail_keyword_row');
  const messageRangeRow = document.getElementById('detail_message_range_row');
  const button = document.getElementById('toggle_detail_actions');
  if(!actionRow || !keywordRow || !messageRangeRow || !button){
    return;
  }
  actionRow.classList.toggle('hidden', !detailActionsVisible);
  keywordRow.classList.toggle('hidden', !detailActionsVisible);
  messageRangeRow.classList.toggle('hidden', !detailActionsVisible);
  button.textContent = detailActionsVisible ? t('detail.actions.hide') : t('detail.actions.show');
}

function setDetailActionsVisible(nextVisible){
  detailActionsVisible = !!nextVisible;
  updateDetailActionsVisibility();
  saveFiltersSoon();
}

function updateDetailMetaVisibility(){
  const meta = document.getElementById('meta');
  const button = document.getElementById('toggle_meta');
  if(!meta || !button){
    return;
  }
  const hasContent = meta.textContent.trim() !== '';
  meta.classList.toggle('hidden', !detailMetaVisible || !hasContent);
  button.textContent = detailMetaVisible ? t('header.meta.hide') : t('header.meta.show');
  button.setAttribute('aria-pressed', detailMetaVisible ? 'true' : 'false');
  button.disabled = !hasContent;
}

function setDetailMetaVisible(nextVisible){
  detailMetaVisible = !!nextVisible;
  updateDetailMetaVisibility();
}

function updateLeftPaneVisibility(){
  const container = document.querySelector('.container');
  const mobileButton = document.getElementById('toggle_session_list_mobile');
  const isMobileLayout = window.matchMedia('(max-width: 900px)').matches;
  if(!container){
    return;
  }
  container.classList.toggle('sidebar-collapsed', isMobileLayout && !leftPaneVisible);
  const label = leftPaneVisible ? t('header.list.hide') : t('header.list.show');
  if(mobileButton){
    mobileButton.textContent = leftPaneVisible ? t('header.list.hideShort') : t('header.list.showShort');
    mobileButton.setAttribute('aria-label', label);
    mobileButton.title = label;
  }
}

function setLeftPaneVisible(nextVisible){
  leftPaneVisible = !!nextVisible;
  updateLeftPaneVisibility();
  saveFiltersSoon();
}

function saveFiltersSoon(){
  if(saveFiltersFrame){
    cancelAnimationFrame(saveFiltersFrame);
  }
  saveFiltersFrame = requestAnimationFrame(() => {
    saveFiltersFrame = 0;
    setTimeout(() => {
      saveFilters();
    }, 0);
  });
}

function cancelScheduledSaveFilters(){
  if(saveFiltersFrame){
    cancelAnimationFrame(saveFiltersFrame);
    saveFiltersFrame = 0;
  }
}

function postJson(url, payload){
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  }).then(r => r.json());
}

function getSelectedSessionLabelFilter(){
  return document.getElementById('session_label_filter').value || '';
}

function getSelectedListEventLabelFilter(){
  return document.getElementById('event_label_filter').value || '';
}

function getSelectedDetailEventLabelFilter(){
  return document.getElementById('detail_event_label_filter').value || '';
}

function isTurnBoundaryFilterEnabled(){
  const checkbox = document.getElementById('turn_boundary_only');
  return !!(checkbox && checkbox.checked);
}

function isSystemLabeledUserEvent(ev){
  if(!ev || ev.kind !== 'message' || ev.role !== 'user'){
    return false;
  }
  const labels = ev.system_labels || [];
  return labels.includes('SKILLS') || labels.includes('CONTINUATION_SUMMARY');
}

function filterEventsToTurnBoundaries(events){
  if(!Array.isArray(events) || events.length === 0){
    return Array.isArray(events) ? events : [];
  }
  const filtered = [];
  let pendingUser = null;
  let lastAssistant = null;

  function flushTurn(){
    if(!pendingUser){
      return;
    }
    filtered.push(pendingUser);
    if(lastAssistant){
      filtered.push(lastAssistant);
    }
    pendingUser = null;
    lastAssistant = null;
  }

  events.forEach(ev => {
    if(ev.kind !== 'message'){
      return;
    }
    if(ev.role === 'user'){
      if(isSystemLabeledUserEvent(ev)){
        return;
      }
      flushTurn();
      pendingUser = ev;
      return;
    }
    if(ev.role === 'assistant' && pendingUser){
      lastAssistant = ev;
    }
  });

  flushTurn();
  return filtered;
}

function populateLabelSelect(selectId, allLabel){
  const select = document.getElementById(selectId);
  const current = select.value;
  const options = [`<option value="">${esc(allLabel)}</option>`].concat(
    state.labels.map(label => `<option value="${esc(label.id)}">${esc(label.name)}</option>`)
  );
  select.innerHTML = options.join('');
  const hasCurrent = state.labels.some(label => String(label.id) === current);
  select.value = hasCurrent ? current : '';
}

function populateLabelControls(){
  populateLabelSelect('session_label_filter', t('filter.sessionLabel.all'));
  populateLabelSelect('event_label_filter', t('filter.eventLabel.all'));
  populateLabelSelect('detail_event_label_filter', t('detail.label.all'));
  ['session_label_filter', 'event_label_filter', 'detail_event_label_filter'].forEach(id => {
    const select = document.getElementById(id);
    const pending = select.dataset.pendingValue;
    if(pending && Array.from(select.options).some(option => option.value === pending)){
      select.value = pending;
    }
    delete select.dataset.pendingValue;
  });
  renderSessionList();
  renderSessionLabelStrip();
  renderActiveSession();
  updateSessionLabelButtonState();
}

function renderAssignedLabels(labels, removeType, extra){
  if(!Array.isArray(labels) || labels.length === 0) return '';
  return labels.map(label => {
    const attrs = removeType ? (
      ` data-remove-type="${esc(removeType)}"` +
      ` data-label-id="${esc(label.id)}"` +
      (extra && extra.eventId ? ` data-event-id="${esc(extra.eventId)}"` : '')
    ) : '';
    const removeButton = removeType
      ? `<button class="label-remove-button" title="${esc(t('picker.removeLabel'))}"${attrs}>×</button>`
      : '';
    return `<span class="data-label-badge" style="${renderColorStyle(label.color_value)}"><span class="label-dot"></span><span>${esc(label.name)}</span>${removeButton}</span>`;
  }).join('');
}

function updateSessionLabelButtonState(){
  const button = document.getElementById('add_session_label');
  button.disabled = !state.activePath || state.labels.length === 0;
}

function renderSessionLabelStrip(){
  const strip = document.getElementById('session_label_strip');
  if(!state.activeSession){
    strip.classList.add('empty');
    strip.textContent = state.isDetailLoading && state.activePath
      ? t('session.labels.loading')
      : t('session.labels.empty');
    updateSessionLabelButtonState();
    return;
  }
  const labels = state.activeSession.session_labels || [];
  if(!labels.length){
    strip.classList.add('empty');
    strip.textContent = t('session.labels.empty');
    updateSessionLabelButtonState();
    return;
  }
  strip.classList.remove('empty');
  strip.innerHTML = renderAssignedLabels(labels, 'session');
  strip.querySelectorAll('.label-remove-button').forEach(button => {
    button.onclick = async () => {
      const labelId = Number(button.dataset.labelId);
      await removeSessionLabel(labelId);
    };
  });
  updateSessionLabelButtonState();
}

function getDetailEventKey(ev, fallbackIndex){
  if(ev && ev.event_id){
    return String(ev.event_id);
  }
  return `${ev && ev.kind ? ev.kind : 'event'}:${ev && ev.timestamp ? ev.timestamp : ''}:${fallbackIndex}`;
}

function shouldShowSystemLabels(){
  const onlyUserInput = document.getElementById('only_user_instruction');
  return !(onlyUserInput && onlyUserInput.checked);
}

function buildEventCardHtml(ev, selectedEventLabelId, fallbackIndex, searchMeta){
  const role = ev.role || 'system';
  const roleLabel = role.replace('_', ' ');
  const labels = ev.labels || [];
  const systemLabels = ev.system_labels || [];
  const matchesSelectedLabel = selectedEventLabelId && labels.some(label => String(label.id) === selectedEventLabelId);
  const eventKey = getDetailEventKey(ev, fallbackIndex);
  const bodyText = getEventBodyText(ev);
  const eventMatches = searchMeta && searchMeta.matchesByEvent ? (searchMeta.matchesByEvent.get(eventKey) || []) : [];
  const body = `<pre>${renderHighlightedEventBody(bodyText, eventMatches)}</pre>`;
  const selectionKey = getEventSelectionKey(ev);
  const isSelectable = state.isEventSelectionMode && isSelectableMessageEvent(ev);
  const isSelected = selectionKey && state.selectedEventIds.has(selectionKey);
  const isRangeSelectable = state.isMessageRangeSelectionMode && isSelectableMessageEvent(ev);
  const isRangeSelected = selectionKey && state.selectedMessageRangeEventId === selectionKey;
  const selectionCheckboxHtml = isSelectable
    ? `<label class="event-select-toggle"><input type="checkbox" class="event-select-checkbox" data-event-id="${esc(selectionKey)}" ${isSelected ? 'checked' : ''} />${esc(t('detail.selectMode'))}</label>`
    : '';
  const rangeSelectionHtml = isRangeSelectable
    ? `<label class="event-range-toggle"><input type="radio" name="message-range-selection" class="event-range-radio" data-event-id="${esc(selectionKey)}" ${isRangeSelected ? 'checked' : ''} />${esc(t('detail.rangeMode'))}</label>`
    : '';
  const labelsHtml = renderAssignedLabels(labels, 'event', { eventId: ev.event_id });
  const copyButtonHtml = ev.kind === 'message'
    ? `<button class="event-copy-button" data-event-id="${esc(ev.event_id || '')}">${esc(t('copy.single'))}</button>`
    : '';
  const systemLabelsHtml = shouldShowSystemLabels()
    ? systemLabels.map(label => `<span class="badge-kind badge-system-label">${esc(label)}</span>`).join('')
    : '';
  return `<div class="ev ${role} ${matchesSelectedLabel ? 'label-match' : ''} ${isSelected ? 'copy-selected' : ''} ${isRangeSelected ? 'range-anchor-selected' : ''}"><div class="ev-head">${selectionCheckboxHtml}${rangeSelectionHtml}<span class="badge-kind">${esc(ev.kind || 'event')}</span><span class="badge-role ${role}">${esc(roleLabel)}</span><span class="badge-time">${esc(fmt(ev.timestamp))}</span>${systemLabelsHtml}<span class="event-actions">${labelsHtml}<button class="event-label-add-button" data-event-id="${esc(ev.event_id || '')}" ${state.labels.length ? '' : 'disabled'}>${esc(t('picker.addLabel'))}</button>${copyButtonHtml}</span></div>${body}</div>`;
}

function attachVisibleEventCardHandlers(eventsBox){
  eventsBox.querySelectorAll('.event-label-add-button').forEach(button => {
    button.onclick = async () => {
      await addEventLabelFromButton(button, button.dataset.eventId);
    };
  });
  eventsBox.querySelectorAll('.event-copy-button').forEach(button => {
    button.onclick = async () => {
      await copyEventMessage(button, button.dataset.eventId);
    };
  });
  eventsBox.querySelectorAll('.event-select-checkbox').forEach(input => {
    input.onchange = () => {
      updateEventSelection(input.dataset.eventId, input.checked, input.closest('.ev'));
    };
  });
  eventsBox.querySelectorAll('.event-range-radio').forEach(input => {
    input.onchange = () => {
      if(input.checked){
        updateMessageRangeSelection(input.dataset.eventId);
      }
    };
  });
  eventsBox.querySelectorAll('.label-remove-button[data-remove-type="event"]').forEach(button => {
    button.onclick = async () => {
      await removeEventLabel(button.dataset.eventId, Number(button.dataset.labelId));
    };
  });
}

function renderEventList(eventsBox, displayEvents, selectedEventLabelId, searchMeta){
  const targetMatch = searchMeta && pendingDetailKeywordFocusIndex >= 0
    ? searchMeta.matches[pendingDetailKeywordFocusIndex] || null
    : null;
  const previousScrollTop = eventsBox.scrollTop;
  eventsBox.innerHTML = displayEvents.map((ev, index) => buildEventCardHtml(ev, selectedEventLabelId, index, searchMeta)).join('');
  eventsBox.scrollTop = previousScrollTop;
  attachVisibleEventCardHandlers(eventsBox);
  if(targetMatch){
    requestAnimationFrame(() => {
      focusDetailKeywordMatch(eventsBox, pendingDetailKeywordFocusIndex);
      pendingDetailKeywordFocusIndex = -1;
    });
  }
}

function hideLabelPicker(){
  const picker = document.getElementById('label_picker');
  picker.classList.add('hidden');
  picker.innerHTML = '';
  labelPickerHandler = null;
}

function showLabelPicker(anchor, onSelect){
  const picker = document.getElementById('label_picker');
  if(!state.labels.length){
    alert(t('picker.noLabels'));
    return;
  }
  labelPickerHandler = onSelect;
  picker.innerHTML = state.labels.map(label =>
    `<button class="label-picker-option" data-label-id="${esc(label.id)}" style="${renderColorStyle(label.color_value)}"><span class="label-dot"></span><span>${esc(label.name)}</span></button>`
  ).join('');
  picker.querySelectorAll('.label-picker-option').forEach(button => {
    button.onclick = async () => {
      const labelId = Number(button.dataset.labelId);
      const handler = labelPickerHandler;
      hideLabelPicker();
      if(!handler){
        return;
      }
      await handler(labelId);
    };
  });
  const rect = anchor.getBoundingClientRect();
  picker.style.top = `${Math.round(rect.bottom + 8)}px`;
  picker.style.left = `${Math.round(Math.min(rect.left, window.innerWidth - 300))}px`;
  picker.classList.remove('hidden');
}

async function loadLabels(reloadSessions){
  const r = await fetch('/api/labels?ts=' + Date.now(), { cache: 'no-store' });
  const data = await r.json();
  const prev = JSON.stringify(state.labels);
  state.labels = data.labels || [];
  populateLabelControls();
  if(reloadSessions && prev !== JSON.stringify(state.labels)){
    await loadSessions({ mode: 'labels' });
  }
}

function openLabelManagerWindow(){
  const features = 'width=720,height=680,resizable=yes,scrollbars=yes';
  if(labelManagerWindow && !labelManagerWindow.closed){
    labelManagerWindow.focus();
    return;
  }
  labelManagerWindow = window.open(`/labels?lang=${encodeURIComponent(uiLanguage)}`, 'claude_label_manager', features);
}

function highlightSessionPath(s){
  const safe = esc(s);
  return safe.replace(/(\\d{4}-\\d{2}-\\d{2}T\\d{2}[-:]\\d{2}[-:]\\d{2}(?:[-:]\\d{3,6})?)/g, '<span class="ts">$1</span>');
}

function normalizeSource(source){
  const raw = (source || '').toLowerCase();
  if(raw === 'claude_desktop' || raw === 'desktop') return 'desktop';
  return 'cli';
}

function sourceLabel(source){
  return normalizeSource(source) === 'desktop' ? 'Claude Desktop' : 'Claude Code CLI';
}

function hasSubagentsSegment(session){
  const candidates = [
    session && session.path ? String(session.path) : '',
    session && session.relative_path ? String(session.relative_path) : '',
    session && session.project ? String(session.project) : '',
  ];
  return candidates.some(value => {
    const normalized = String(value || '').toLowerCase();
    return normalized.includes('/subagents/')
      || normalized.includes('\\\\subagents\\\\')
      || normalized.includes('/subagents\\\\')
      || normalized.includes('\\\\subagents/');
  });
}

function normalizeSourceFilter(source){
  const raw = (source || '').toLowerCase();
  if(raw === '' || raw === 'all') return 'all';
  return raw === 'claude_desktop' || raw === 'desktop' ? 'claude_desktop' : 'claude_cli';
}

function normalizeSubagentsFilter(value){
  const raw = (value || '').toLowerCase();
  if(raw === 'exclude') return 'exclude';
  return 'include';
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
  const iso = parseDateInputToIso(raw);
  if(!iso) return null;
  const ts = toTimestamp(`${iso}T00:00:00`);
  return Number.isNaN(ts) ? null : ts;
}

function parseOptionalDateEnd(raw){
  if(!raw) return null;
  const iso = parseDateInputToIso(raw);
  if(!iso) return null;
  const ts = toTimestamp(`${iso}T23:59:59.999`);
  return Number.isNaN(ts) ? null : ts;
}

function pad2(value){
  return String(value).padStart(2, '0');
}

function parseDateInputToIso(raw){
  if(typeof raw !== 'string') return '';
  const trimmed = raw.trim();
  if(!trimmed) return '';
  const canonical = trimmed
    .replace(/\u3000/g, ' ')
    .replace(/[年月]/g, '/')
    .replace(/日/g, ' ')
    .replace(/[．。]/g, '.')
    .replace(/\s*\/\s*/g, '/')
    .replace(/\s+/g, ' ');
  let m = canonical.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if(!m){
    m = canonical.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})$/);
  }
  if(!m){
    m = canonical.match(/(\d{4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,2})/);
  }
  if(!m){
    return '';
  }
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if(!Number.isFinite(year) || year < 1900 || year > 2999) return '';
  if(!Number.isFinite(month) || month < 1 || month > 12) return '';
  if(!Number.isFinite(day) || day < 1 || day > 31) return '';
  const d = new Date(year, month - 1, day, 0, 0, 0, 0);
  if(d.getFullYear() !== year || d.getMonth() !== month - 1 || d.getDate() !== day){
    return '';
  }
  return `${year}-${pad2(month)}-${pad2(day)}`;
}

function formatDateInputFromIso(isoValue){
  const iso = parseDateInputToIso(isoValue);
  if(!iso) return '';
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if(!m) return '';
  return `${m[1]} / ${m[2]} / ${m[3]}`;
}

function normalizeDateInputDisplay(raw){
  const iso = parseDateInputToIso(raw);
  return iso ? formatDateInputFromIso(iso) : '';
}

function parseDateTimeInputToIso(raw){
  if(typeof raw !== 'string') return '';
  const trimmed = raw.trim();
  if(!trimmed) return '';
  const canonical = trimmed
    .replace(/\u3000/g, ' ')
    .replace(/[年月]/g, '/')
    .replace(/日/g, ' ')
    .replace(/[：]/g, ':')
    .replace(/[．。]/g, '.')
    .replace(/\s*\/\s*/g, '/')
    .replace(/\s+/g, ' ');
  let m = canonical.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2})?$/);
  if(!m){
    m = canonical.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2}) (\d{1,2}):(\d{2})(?::\d{1,2})?$/);
  }
  if(!m){
    m = canonical.match(/(\d{4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,2})[ T](\d{1,2}):(\d{1,2})(?::\d{1,2})?/);
  }
  if(!m){
    return '';
  }
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  const hour = Number(m[4]);
  const minute = Number(m[5]);
  if(!Number.isFinite(year) || year < 1900 || year > 2999) return '';
  if(!Number.isFinite(month) || month < 1 || month > 12) return '';
  if(!Number.isFinite(day) || day < 1 || day > 31) return '';
  if(!Number.isFinite(hour) || hour < 0 || hour > 23) return '';
  if(!Number.isFinite(minute) || minute < 0 || minute > 59) return '';
  const d = new Date(year, month - 1, day, hour, minute, 0, 0);
  if(
    d.getFullYear() !== year ||
    d.getMonth() !== month - 1 ||
    d.getDate() !== day ||
    d.getHours() !== hour ||
    d.getMinutes() !== minute
  ){
    return '';
  }
  return `${year}-${pad2(month)}-${pad2(day)}T${pad2(hour)}:${pad2(minute)}`;
}

function formatDateTimeInputFromIso(isoValue){
  const iso = parseDateTimeInputToIso(isoValue);
  if(!iso) return '';
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
  if(!m) return '';
  return `${m[1]} / ${m[2]} / ${m[3]} ${m[4]}:${m[5]}`;
}

function normalizeDatetimeInputDisplay(raw){
  const iso = parseDateTimeInputToIso(raw);
  return iso ? formatDateTimeInputFromIso(iso) : '';
}

function parseTimeInputToValue(raw){
  if(typeof raw !== 'string') return '';
  const trimmed = raw.trim();
  if(!trimmed) return '';
  const canonical = trimmed
    .replace(/[：]/g, ':')
    .replace(/\s+/g, '');
  const m = canonical.match(/^(\d{1,2}):(\d{2})(?::\d{1,2})?$/);
  if(!m){
    return '';
  }
  const hour = Number(m[1]);
  const minute = Number(m[2]);
  if(!Number.isFinite(hour) || hour < 0 || hour > 23) return '';
  if(!Number.isFinite(minute) || minute < 0 || minute > 59) return '';
  return `${pad2(hour)}:${pad2(minute)}`;
}

function buildDateTimeIsoFromParts(dateRaw, timeRaw, boundary){
  const dateIso = parseDateInputToIso(dateRaw);
  if(!dateIso){
    return '';
  }
  const timeValue = parseTimeInputToValue(timeRaw);
  const fallbackTime = boundary === 'end' ? '23:59' : '00:00';
  return `${dateIso}T${timeValue || fallbackTime}`;
}

function extractTimeInputFromIso(isoValue){
  const iso = parseDateTimeInputToIso(isoValue);
  if(!iso) return '';
  const m = iso.match(/T(\d{2}):(\d{2})$/);
  if(!m) return '';
  return `${m[1]}:${m[2]}`;
}

function applyDatePasteValue(input, raw){
  if(!input){
    return false;
  }
  const dateIso = parseDateInputToIso(raw);
  if(!dateIso){
    return false;
  }
  input.value = dateIso;
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

function applyDateTimePairPasteValue(dateInput, timeInput, target, raw){
  if(!dateInput || !timeInput || !target){
    return false;
  }
  const dateTimeIso = parseDateTimeInputToIso(raw);
  if(dateTimeIso){
    dateInput.value = parseDateInputToIso(dateTimeIso);
    timeInput.value = extractTimeInputFromIso(dateTimeIso);
    syncDateTimeInputPairState(dateInput.id, timeInput.id);
    target.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }
  if(target === dateInput){
    const dateIso = parseDateInputToIso(raw);
    if(!dateIso){
      return false;
    }
    dateInput.value = dateIso;
    syncDateTimeInputPairState(dateInput.id, timeInput.id);
    dateInput.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }
  const timeValue = parseTimeInputToValue(raw);
  if(!timeValue || !parseDateInputToIso(dateInput.value)){
    return false;
  }
  timeInput.value = timeValue;
  syncDateTimeInputPairState(dateInput.id, timeInput.id);
  timeInput.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

function setDateTimePairFromIso(dateId, timeId, isoValue){
  const dateInput = document.getElementById(dateId);
  const timeInput = document.getElementById(timeId);
  if(dateInput){
    dateInput.value = parseDateInputToIso(isoValue);
  }
  if(timeInput){
    timeInput.value = extractTimeInputFromIso(isoValue);
  }
  syncDateTimeInputPairState(dateId, timeId);
}

function syncDateTimeInputPairState(dateId, timeId){
  const dateInput = document.getElementById(dateId);
  const timeInput = document.getElementById(timeId);
  if(!dateInput || !timeInput){
    return;
  }
  const requiresActiveSession = dateId.startsWith('detail_');
  const hasControlAccess = !requiresActiveSession || !!state.activeSession;
  const hasDate = Boolean(parseDateInputToIso(dateInput.value));
  if(!hasDate){
    timeInput.value = '';
  } else if(timeInput.value){
    timeInput.value = parseTimeInputToValue(timeInput.value);
  }
  dateInput.disabled = !hasControlAccess;
  timeInput.disabled = !hasControlAccess || !hasDate;
}

function refreshDateTimeInputPairStates(){
  syncDateTimeInputPairState('event_date_from_date', 'event_date_from_time');
  syncDateTimeInputPairState('event_date_to_date', 'event_date_to_time');
  syncDateTimeInputPairState('detail_event_date_from_date', 'detail_event_date_from_time');
  syncDateTimeInputPairState('detail_event_date_to_date', 'detail_event_date_to_time');
}

const DATETIME_INPUT_SKELETON = '0000 / 00 / 00 --:--';
const DATETIME_INPUT_SEGMENTS = [
  { start: 0, end: 4, fill: '0' },
  { start: 7, end: 9, fill: '0' },
  { start: 12, end: 14, fill: '0' },
  { start: 15, end: 17, fill: '-' },
  { start: 18, end: 20, fill: '-' },
];

function getDateTimeSegmentIndexByPos(pos){
  const safePos = Number.isFinite(pos) ? pos : 0;
  for(let i = 0; i < DATETIME_INPUT_SEGMENTS.length; i += 1){
    const seg = DATETIME_INPUT_SEGMENTS[i];
    if(safePos >= seg.start && safePos <= seg.end){
      return i;
    }
  }
  if(safePos < DATETIME_INPUT_SEGMENTS[0].start){
    return 0;
  }
  return DATETIME_INPUT_SEGMENTS.length - 1;
}

function selectDateTimeSegment(input, index){
  const safeIndex = Math.max(0, Math.min(DATETIME_INPUT_SEGMENTS.length - 1, index));
  const seg = DATETIME_INPUT_SEGMENTS[safeIndex];
  input.setSelectionRange(seg.start, seg.end);
}

function setDateTimeSegment(inputValue, index, segmentValue){
  const seg = DATETIME_INPUT_SEGMENTS[index];
  return inputValue.slice(0, seg.start) + segmentValue + inputValue.slice(seg.end);
}

function shiftDateTimeSegment(currentSegment, digit, fillChar){
  const len = currentSegment.length;
  const normalized = currentSegment.replace(/[^0-9]/g, '').padStart(len, fillChar === '-' ? '0' : fillChar).slice(-len);
  const shifted = normalized.slice(1) + digit;
  if(fillChar === '-'){
    const allZero = /^0+$/.test(shifted);
    if(allZero){
      return '-'.repeat(len);
    }
  }
  return shifted;
}

function setupDateTimeSegmentInput(input){
  if(!input || input.dataset.segmentedReady === '1'){
    return;
  }
  input.dataset.segmentedReady = '1';
  const ensureSkeleton = () => {
    if(!input.value){
      input.value = DATETIME_INPUT_SKELETON;
    }
  };
  input.addEventListener('focus', () => {
    ensureSkeleton();
    selectDateTimeSegment(input, getDateTimeSegmentIndexByPos(input.selectionStart || 0));
  });
  input.addEventListener('click', () => {
    ensureSkeleton();
    selectDateTimeSegment(input, getDateTimeSegmentIndexByPos(input.selectionStart || 0));
  });
  input.addEventListener('keydown', (event) => {
    if(!/^\d$/.test(event.key) && event.key !== 'Backspace' && event.key !== 'Delete' && event.key !== 'ArrowLeft' && event.key !== 'ArrowRight' && event.key !== 'Tab' && event.key !== '/' && event.key !== ':' && event.key !== ' '){
      return;
    }
    ensureSkeleton();
    let segmentIndex = getDateTimeSegmentIndexByPos(input.selectionStart || 0);
    if(/^\d$/.test(event.key)){
      event.preventDefault();
      const seg = DATETIME_INPUT_SEGMENTS[segmentIndex];
      const current = input.value.slice(seg.start, seg.end);
      const next = shiftDateTimeSegment(current, event.key, seg.fill);
      input.value = setDateTimeSegment(input.value, segmentIndex, next);
      selectDateTimeSegment(input, segmentIndex);
      return;
    }
    if(event.key === 'Backspace' || event.key === 'Delete'){
      event.preventDefault();
      const seg = DATETIME_INPUT_SEGMENTS[segmentIndex];
      input.value = setDateTimeSegment(input.value, segmentIndex, seg.fill.repeat(seg.end - seg.start));
      selectDateTimeSegment(input, segmentIndex);
      return;
    }
    if(event.key === 'ArrowLeft'){
      event.preventDefault();
      selectDateTimeSegment(input, Math.max(0, segmentIndex - 1));
      return;
    }
    if(event.key === 'ArrowRight' || event.key === '/' || event.key === ':' || event.key === ' '){
      event.preventDefault();
      selectDateTimeSegment(input, Math.min(DATETIME_INPUT_SEGMENTS.length - 1, segmentIndex + 1));
      return;
    }
    if(event.key === 'Tab'){
      if(event.shiftKey){
        selectDateTimeSegment(input, Math.max(0, segmentIndex - 1));
      } else {
        selectDateTimeSegment(input, Math.min(DATETIME_INPUT_SEGMENTS.length - 1, segmentIndex + 1));
      }
    }
  });
  input.addEventListener('blur', () => {
    const display = normalizeDatetimeInputDisplay(input.value);
    if(display){
      input.value = display;
      return;
    }
    if(input.value === DATETIME_INPUT_SKELETON){
      input.value = '';
    }
  });
  input.addEventListener('input', (event) => {
    if(event && typeof event.inputType === 'string' && event.inputType !== 'insertFromPaste'){
      return;
    }
    const iso = parseDateTimeInputToIso(input.value || '');
    if(!iso){
      return;
    }
    input.value = formatDateTimeInputFromIso(iso);
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  input.addEventListener('paste', (event) => {
    const text = event.clipboardData ? event.clipboardData.getData('text') : '';
    const iso = parseDateTimeInputToIso(text || '');
    if(iso){
      event.preventDefault();
      input.value = formatDateTimeInputFromIso(iso);
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }
    setTimeout(() => {
      const fallbackIso = parseDateTimeInputToIso(input.value || '');
      if(!fallbackIso){
        return;
      }
      input.value = formatDateTimeInputFromIso(fallbackIso);
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }, 0);
  });
}

const DATE_INPUT_SKELETON = '0000 / 00 / 00';
const DATE_INPUT_SEGMENTS = [
  { start: 0, end: 4, fill: '0' },
  { start: 7, end: 9, fill: '0' },
  { start: 12, end: 14, fill: '0' },
];

function getDateSegmentIndexByPos(pos){
  const safePos = Number.isFinite(pos) ? pos : 0;
  for(let i = 0; i < DATE_INPUT_SEGMENTS.length; i += 1){
    const seg = DATE_INPUT_SEGMENTS[i];
    if(safePos >= seg.start && safePos <= seg.end){
      return i;
    }
  }
  if(safePos < DATE_INPUT_SEGMENTS[0].start){
    return 0;
  }
  return DATE_INPUT_SEGMENTS.length - 1;
}

function selectDateSegment(input, index){
  const safeIndex = Math.max(0, Math.min(DATE_INPUT_SEGMENTS.length - 1, index));
  const seg = DATE_INPUT_SEGMENTS[safeIndex];
  input.setSelectionRange(seg.start, seg.end);
}

function setDateSegment(inputValue, index, segmentValue){
  const seg = DATE_INPUT_SEGMENTS[index];
  return inputValue.slice(0, seg.start) + segmentValue + inputValue.slice(seg.end);
}

function setupDateSegmentInput(input){
  if(!input || input.dataset.segmentedDateReady === '1'){
    return;
  }
  input.dataset.segmentedDateReady = '1';
  const ensureSkeleton = () => {
    if(!input.value){
      input.value = DATE_INPUT_SKELETON;
    }
  };
  input.addEventListener('focus', () => {
    ensureSkeleton();
    selectDateSegment(input, getDateSegmentIndexByPos(input.selectionStart || 0));
  });
  input.addEventListener('click', () => {
    ensureSkeleton();
    selectDateSegment(input, getDateSegmentIndexByPos(input.selectionStart || 0));
  });
  input.addEventListener('keydown', (event) => {
    if(!/^\d$/.test(event.key) && event.key !== 'Backspace' && event.key !== 'Delete' && event.key !== 'ArrowLeft' && event.key !== 'ArrowRight' && event.key !== 'Tab' && event.key !== '/' && event.key !== ' '){
      return;
    }
    ensureSkeleton();
    const segmentIndex = getDateSegmentIndexByPos(input.selectionStart || 0);
    if(/^\d$/.test(event.key)){
      event.preventDefault();
      const seg = DATE_INPUT_SEGMENTS[segmentIndex];
      const current = input.value.slice(seg.start, seg.end);
      const next = shiftDateTimeSegment(current, event.key, seg.fill);
      input.value = setDateSegment(input.value, segmentIndex, next);
      selectDateSegment(input, segmentIndex);
      return;
    }
    if(event.key === 'Backspace' || event.key === 'Delete'){
      event.preventDefault();
      const seg = DATE_INPUT_SEGMENTS[segmentIndex];
      input.value = setDateSegment(input.value, segmentIndex, seg.fill.repeat(seg.end - seg.start));
      selectDateSegment(input, segmentIndex);
      return;
    }
    if(event.key === 'ArrowLeft'){
      event.preventDefault();
      selectDateSegment(input, Math.max(0, segmentIndex - 1));
      return;
    }
    if(event.key === 'ArrowRight' || event.key === '/' || event.key === ' '){
      event.preventDefault();
      selectDateSegment(input, Math.min(DATE_INPUT_SEGMENTS.length - 1, segmentIndex + 1));
      return;
    }
    if(event.key === 'Tab'){
      if(event.shiftKey){
        selectDateSegment(input, Math.max(0, segmentIndex - 1));
      } else {
        selectDateSegment(input, Math.min(DATE_INPUT_SEGMENTS.length - 1, segmentIndex + 1));
      }
    }
  });
  input.addEventListener('blur', () => {
    const display = normalizeDateInputDisplay(input.value);
    if(display){
      input.value = display;
      return;
    }
    if(input.value === DATE_INPUT_SKELETON){
      input.value = '';
    }
  });
  input.addEventListener('input', (event) => {
    if(event && typeof event.inputType === 'string' && event.inputType !== 'insertFromPaste'){
      return;
    }
    const iso = parseDateInputToIso(input.value || '');
    if(!iso){
      return;
    }
    input.value = formatDateInputFromIso(iso);
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  input.addEventListener('paste', (event) => {
    const text = event.clipboardData ? event.clipboardData.getData('text') : '';
    const iso = parseDateInputToIso(text || '');
    if(iso){
      event.preventDefault();
      input.value = formatDateInputFromIso(iso);
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }
    setTimeout(() => {
      const fallbackIso = parseDateInputToIso(input.value || '');
      if(!fallbackIso){
        return;
      }
      input.value = formatDateInputFromIso(fallbackIso);
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }, 0);
  });
}

function destroyDateTimePickers(){
  dateTimePickers.forEach((picker) => {
    try {
      picker.destroy();
    } catch (e) {
      // Ignore cleanup errors.
    }
  });
  dateTimePickers = [];
}

function destroyDatePickers(){
  datePickers.forEach((picker) => {
    try {
      picker.destroy();
    } catch (e) {
      // Ignore cleanup errors.
    }
  });
  datePickers = [];
}

function bindCalendarTriggerButtons(){
  document.querySelectorAll('.datetime-trigger').forEach((button) => {
    const target = button.getAttribute('data-target') || '';
    button.onclick = () => {
      const allPickers = datePickers.concat(dateTimePickers);
      const picker = allPickers.find((item) => item && item.input && item.input.id === target);
      if(picker){
        picker.open();
      }
    };
  });
}

function initDatePickers(){
  destroyDatePickers();
  if(typeof flatpickr !== 'function'){
    return;
  }
  const attachPicker = (inputId, onApply) => {
    const input = document.getElementById(inputId);
    if(!input) return;
    setupDateSegmentInput(input);
    const normalized = normalizeDateInputDisplay(input.value);
    if(input.value !== normalized){
      input.value = normalized;
    }
    let beforeOpenValue = input.value;
    const picker = flatpickr(input, {
      enableTime: false,
      dateFormat: 'Y / m / d',
      allowInput: true,
      clickOpens: false,
      onOpen: () => {
        beforeOpenValue = normalizeDateInputDisplay(input.value);
      },
      onClose: () => {
        const currentValue = normalizeDateInputDisplay(input.value);
        if(input.value !== currentValue){
          input.value = currentValue;
        }
        if(currentValue !== beforeOpenValue){
          onApply();
        }
      },
    });
    datePickers.push(picker);
  };
  attachPicker('date_from', applyFilter);
  attachPicker('date_to', applyFilter);
  bindCalendarTriggerButtons();
}

function initDateTimePickers(){
  destroyDateTimePickers();
  if(typeof flatpickr !== 'function'){
    return;
  }
  const confirmPluginFactory = window.confirmDatePlugin;
  const createPlugins = () => {
    if(typeof confirmPluginFactory !== 'function'){
      return [];
    }
    return [confirmPluginFactory({ confirmText: 'OK', showAlways: true, theme: 'light' })];
  };
  const attachPicker = (inputId, onApply) => {
    const input = document.getElementById(inputId);
    if(!input) return;
    setupDateTimeSegmentInput(input);
    const normalized = normalizeDatetimeInputDisplay(input.value);
    if(input.value !== normalized){
      input.value = normalized;
    }
    let beforeOpenValue = input.value;
    const picker = flatpickr(input, {
      enableTime: true,
      time_24hr: true,
      enableSeconds: false,
      minuteIncrement: 1,
      dateFormat: 'Y / m / d H:i',
      allowInput: true,
      clickOpens: false,
      plugins: createPlugins(),
      onOpen: () => {
        beforeOpenValue = normalizeDatetimeInputDisplay(input.value);
      },
      onReady: (_selectedDates, _dateStr, instance) => {
        const calendar = instance.calendarContainer;
        if(!calendar) return;
        const actions = document.createElement('div');
        actions.className = 'flatpickr-extra-actions';
        const clearButton = document.createElement('button');
        clearButton.type = 'button';
        clearButton.textContent = '削除';
        clearButton.addEventListener('click', () => {
          instance.clear();
          instance.close();
        });
        const todayButton = document.createElement('button');
        todayButton.type = 'button';
        todayButton.textContent = '今日';
        todayButton.addEventListener('click', () => {
          instance.setDate(new Date(), false);
          instance.close();
        });
        actions.appendChild(clearButton);
        actions.appendChild(todayButton);
        const confirmRow = calendar.querySelector('.flatpickr-confirm');
        if(confirmRow && confirmRow.parentNode){
          confirmRow.parentNode.insertBefore(actions, confirmRow);
        } else {
          calendar.appendChild(actions);
        }
      },
      onClose: () => {
        const currentValue = normalizeDatetimeInputDisplay(input.value);
        if(input.value !== currentValue){
          input.value = currentValue;
        }
        if(currentValue !== beforeOpenValue){
          onApply();
        }
      },
    });
    dateTimePickers.push(picker);
  };
  attachPicker('event_date_from', applyFilter);
  attachPicker('event_date_to', applyFilter);
  attachPicker('detail_event_date_from', () => {
    saveFilters();
    renderActiveSession();
  });
  attachPicker('detail_event_date_to', () => {
    saveFilters();
    renderActiveSession();
  });
  bindCalendarTriggerButtons();
}

function parseOptionalDatetimeStart(raw){
  if(!raw) return null;
  const iso = parseDateTimeInputToIso(raw);
  if(!iso) return null;
  const ts = toTimestamp(iso);
  return Number.isNaN(ts) ? null : ts;
}

function parseOptionalDatetimeEnd(raw){
  if(!raw) return null;
  const iso = parseDateTimeInputToIso(raw);
  if(!iso) return null;
  const ts = toTimestamp(iso);
  if(Number.isNaN(ts)) return null;
  return ts + 59999;
}

function getActiveSessionId(){
  if(!state.activeSession) return '';
  return (state.activeSession.session_id || state.activeSession.id || '').toString().trim();
}

function getButtonLabel(button, fallback){
  if(!button) return fallback || '';
  if(!button.dataset.defaultLabel){
    button.dataset.defaultLabel = button.textContent;
  }
  return button.dataset.defaultLabel || fallback || '';
}

function flashButtonLabel(button, temporaryLabel, fallback, duration){
  if(!button) return;
  const defaultLabel = getButtonLabel(button, fallback);
  button.textContent = temporaryLabel;
  if(button._labelTimer){
    clearTimeout(button._labelTimer);
  }
  button._labelTimer = setTimeout(() => {
    button.textContent = defaultLabel;
  }, duration || BUTTON_FEEDBACK_MS);
}

function waitForUiFeedback(duration){
  return new Promise(resolve => {
    setTimeout(resolve, duration || BUTTON_FEEDBACK_MS);
  });
}

function getDetailKeywordInputValue(){
  const input = document.getElementById('detail_keyword_q');
  return input ? input.value : '';
}

function stringifyEventBodyValue(value){
  if(value == null){
    return '';
  }
  if(typeof value === 'string'){
    return value;
  }
  if(typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint'){
    return String(value);
  }
  try {
    return JSON.stringify(value, (key, currentValue) => {
      if(typeof currentValue === 'string' && currentValue.startsWith('data:image/')){
        return '[image data omitted]';
      }
      return currentValue;
    }, 2) || '';
  } catch (error) {
    return String(value);
  }
}

function containsLiteralKeyword(text, keyword){
  if(!keyword){
    return false;
  }
  return stringifyEventBodyValue(text).toLocaleLowerCase().includes(keyword.toLocaleLowerCase());
}

function findLiteralKeywordRanges(text, keyword){
  if(!keyword){
    return [];
  }
  const source = stringifyEventBodyValue(text);
  const haystack = source.toLocaleLowerCase();
  const needle = keyword.toLocaleLowerCase();
  const ranges = [];
  let cursor = 0;
  while(cursor <= haystack.length - needle.length){
    const nextIndex = haystack.indexOf(needle, cursor);
    if(nextIndex === -1){
      break;
    }
    ranges.push({ start: nextIndex, end: nextIndex + keyword.length });
    cursor = nextIndex + Math.max(keyword.length, 1);
  }
  return ranges;
}

function getEventBodyText(ev){
  if(!ev){
    return '';
  }
  if(ev.kind === 'message' || ev.kind === 'agent_update'){
    return stringifyEventBodyValue(ev.text);
  }
  if(ev.kind === 'function_call'){
    return `name: ${stringifyEventBodyValue(ev.name)}\n${stringifyEventBodyValue(ev.arguments)}`;
  }
  if(ev.kind === 'function_output'){
    return stringifyEventBodyValue(ev.output);
  }
  try {
    return JSON.stringify(ev, null, 2) || '';
  } catch (error) {
    return '';
  }
}

function buildDetailKeywordSearchMeta(displayEvents, keyword){
  const matches = [];
  const matchesByEvent = new Map();
  const rawKeyword = keyword || '';
  if(!rawKeyword){
    return { keyword: '', matches, matchesByEvent, total: 0 };
  }
  displayEvents.forEach((ev, eventIndex) => {
    const eventKey = getDetailEventKey(ev, eventIndex);
    const ranges = findLiteralKeywordRanges(getEventBodyText(ev), rawKeyword);
    if(!ranges.length){
      return;
    }
    const eventMatches = ranges.map(range => {
      const match = {
        eventKey,
        eventIndex,
        start: range.start,
        end: range.end,
        globalIndex: matches.length,
      };
      matches.push(match);
      return match;
    });
    matchesByEvent.set(eventKey, eventMatches);
  });
  return {
    keyword: rawKeyword,
    matches,
    matchesByEvent,
    total: matches.length,
  };
}

function normalizeDetailKeywordSearchPosition(searchMeta){
  if(!searchMeta.total){
    detailKeywordCurrentMatchIndex = -1;
    pendingDetailKeywordFocusIndex = -1;
    return;
  }
  if(detailKeywordCurrentMatchIndex < 0 || detailKeywordCurrentMatchIndex >= searchMeta.total){
    detailKeywordCurrentMatchIndex = 0;
  }
  if(pendingDetailKeywordFocusIndex >= searchMeta.total){
    pendingDetailKeywordFocusIndex = -1;
  }
}

function renderHighlightedEventBody(text, eventMatches){
  if(!Array.isArray(eventMatches) || !eventMatches.length){
    return esc(text || '');
  }
  let cursor = 0;
  let html = '';
  const source = text || '';
  eventMatches.forEach(match => {
    html += esc(source.slice(cursor, match.start));
    const currentClass = match.globalIndex === detailKeywordCurrentMatchIndex ? ' current' : '';
    html += `<mark class="detail-keyword-hit${currentClass}" data-search-match-index="${match.globalIndex}">${esc(source.slice(match.start, match.end))}</mark>`;
    cursor = match.end;
  });
  html += esc(source.slice(cursor));
  return html;
}

function updateDetailKeywordControls(searchMeta){
  const input = document.getElementById('detail_keyword_q');
  const filterButton = document.getElementById('detail_keyword_filter');
  const searchButton = document.getElementById('detail_keyword_search');
  const prevButton = document.getElementById('detail_keyword_prev');
  const nextButton = document.getElementById('detail_keyword_next');
  const clearButton = document.getElementById('detail_keyword_clear');
  if(!input || !filterButton || !searchButton || !prevButton || !nextButton || !clearButton){
    return;
  }
  const hasActiveSession = !!state.activeSession;
  const hasInputValue = getDetailKeywordInputValue() !== '';
  const searchTotal = searchMeta && typeof searchMeta.total === 'number' ? searchMeta.total : detailKeywordSearchTotal;
  const hasSearchMatches = searchTotal > 0;
  const hasKeywordState = hasInputValue || detailKeywordFilterTerm !== '' || detailKeywordSearchTerm !== '';
  input.disabled = !hasActiveSession;
  const hasActiveFilter = detailKeywordFilterTerm !== '';
  filterButton.disabled = !hasActiveSession || (!hasInputValue && !hasActiveFilter);
  searchButton.disabled = !hasActiveSession || !hasInputValue;
  prevButton.disabled = !hasSearchMatches;
  nextButton.disabled = !hasSearchMatches;
  clearButton.disabled = !hasKeywordState;
  filterButton.classList.toggle('active', hasActiveSession && hasActiveFilter);
  filterButton.textContent = hasActiveFilter ? t('detail.searchFilterClear') : t('detail.searchFilter');
  searchButton.classList.toggle('active', hasActiveSession && detailKeywordSearchTerm !== '');
  const matchCountEl = document.getElementById('detail_keyword_match_count');
  if(matchCountEl){
    if(hasSearchMatches){
      const current = detailKeywordCurrentMatchIndex >= 0 ? detailKeywordCurrentMatchIndex + 1 : 0;
      matchCountEl.textContent = t('detail.matchCounter', { current: current, total: searchTotal });
      matchCountEl.classList.remove('hidden');
    } else {
      matchCountEl.textContent = '';
      matchCountEl.classList.add('hidden');
    }
  }
  updateClearDetailButtonState();
}

function updateDetailDisplayControlsState(){
  const hasActiveSession = !!state.activeSession;
  ['only_user_instruction', 'only_ai_response', 'turn_boundary_only', 'reverse_order'].forEach((id) => {
    const input = document.getElementById(id);
    const label = input ? input.closest('.toggle-chip') : null;
    if(input){
      input.disabled = !hasActiveSession;
    }
    if(label){
      label.classList.toggle('disabled', !hasActiveSession);
      label.setAttribute('aria-disabled', hasActiveSession ? 'false' : 'true');
    }
  });
  const detailEventLabelFilter = document.getElementById('detail_event_label_filter');
  if(detailEventLabelFilter){
    detailEventLabelFilter.disabled = !hasActiveSession;
  }
  syncDateTimeInputPairState('detail_event_date_from_date', 'detail_event_date_from_time');
  syncDateTimeInputPairState('detail_event_date_to_date', 'detail_event_date_to_time');
  const clearDetailEventDateButton = document.getElementById('clear_detail_event_date');
  if(clearDetailEventDateButton){
    clearDetailEventDateButton.disabled = !hasActiveSession || !hasDetailEventDateFilter();
  }
}

function resetDetailKeywordState(){
  detailKeywordFilterTerm = '';
  detailKeywordSearchTerm = '';
  detailKeywordCurrentMatchIndex = -1;
  pendingDetailKeywordFocusIndex = -1;
  detailKeywordSearchTotal = 0;
}

function focusDetailKeywordMatch(eventsBox, matchIndex){
  if(matchIndex < 0){
    return;
  }
  const target = eventsBox.querySelector(`.detail-keyword-hit[data-search-match-index="${matchIndex}"]`);
  if(target){
    target.scrollIntoView({ block: 'center', inline: 'nearest' });
  }
}

function isAutomaticSessionsLoadMode(mode){
  return mode === 'auto' || mode === 'focus' || mode === 'labels';
}

function shouldSyncActiveSessionAfterListLoad(mode){
  return mode !== 'auto';
}

function clearDeferredDetailSyncTimer(){
  if(deferredDetailSyncTimer){
    clearTimeout(deferredDetailSyncTimer);
    deferredDetailSyncTimer = 0;
  }
}

function noteDetailInteraction(){
  detailInteractionLockUntil = Date.now() + DETAIL_INTERACTION_LOCK_MS;
}

function hasDetailTextSelection(){
  const eventsBox = document.getElementById('events');
  const selection = window.getSelection ? window.getSelection() : null;
  if(!eventsBox || !selection || selection.isCollapsed || selection.rangeCount === 0){
    return false;
  }
  const anchorNode = selection.anchorNode;
  const focusNode = selection.focusNode;
  return Boolean(
    (anchorNode && eventsBox.contains(anchorNode)) ||
    (focusNode && eventsBox.contains(focusNode))
  );
}

function hasRecentDetailInteraction(){
  return detailPointerDown || hasDetailTextSelection() || Date.now() < detailInteractionLockUntil;
}

function syncActiveSessionSummaryFromList(path){
  if(!path){
    return;
  }
  const summary = (state.sessions || []).find(session => session.path === path);
  if(!summary){
    return;
  }
  state.activeSession = {
    ...(state.activeSession || {}),
    ...summary,
  };
}

async function maybeRunDeferredAutomaticDetailSync(){
  if(!pendingAutomaticDetailSync){
    return;
  }
  if(!document.hasFocus() || hasRecentDetailInteraction() || state.isDetailLoading || !state.activePath){
    scheduleDeferredAutomaticDetailSync();
    return;
  }
  pendingAutomaticDetailSync = false;
  clearDeferredDetailSyncTimer();
  await openSession(state.activePath, { mode: 'sync' });
}

function scheduleDeferredAutomaticDetailSync(){
  clearDeferredDetailSyncTimer();
  if(!pendingAutomaticDetailSync){
    return;
  }
  const waitMs = Math.max(0, detailInteractionLockUntil - Date.now()) + 80;
  deferredDetailSyncTimer = setTimeout(() => {
    deferredDetailSyncTimer = 0;
    void maybeRunDeferredAutomaticDetailSync();
  }, waitMs);
}

async function copyTextToClipboard(text){
  if(!text) return false;
  let copied = false;
  try {
    if(navigator.clipboard && navigator.clipboard.writeText){
      await navigator.clipboard.writeText(text);
      copied = true;
    }
  } catch (e) {
    copied = false;
  }
  if(copied){
    return true;
  }
  const helper = document.createElement('textarea');
  helper.value = text;
  helper.setAttribute('readonly', '');
  helper.style.position = 'fixed';
  helper.style.opacity = '0';
  document.body.appendChild(helper);
  helper.select();
  try {
    copied = document.execCommand('copy');
  } finally {
    document.body.removeChild(helper);
  }
  return copied;
}

function getDisplayMessageEvents(){
  return getDisplayEvents().filter(ev => ev.kind === 'message' && (ev.text || '').trim());
}

function getEventSelectionKey(ev){
  return ev && ev.event_id ? String(ev.event_id) : '';
}

function isSelectableMessageEvent(ev){
  return ev && ev.kind === 'message' && (ev.text || '').trim() && getEventSelectionKey(ev);
}

function getSelectableDisplayMessageEvents(){
  return getDisplayEvents().filter(isSelectableMessageEvent);
}

function getSelectedMessageEvents(){
  const selectedIds = state.selectedEventIds || new Set();
  return (state.activeEvents || []).filter(ev => isSelectableMessageEvent(ev) && selectedIds.has(getEventSelectionKey(ev)));
}

function getSelectedMessageRangeEvent(){
  const selectedId = state.selectedMessageRangeEventId || '';
  if(!selectedId){
    return null;
  }
  return (state.activeEvents || []).find(ev => isSelectableMessageEvent(ev) && getEventSelectionKey(ev) === selectedId) || null;
}

function clearSelectedEventIds(){
  state.selectedEventIds = new Set();
}

function syncSelectedEventIdsToActiveEvents(){
  const validIds = new Set((state.activeEvents || []).filter(isSelectableMessageEvent).map(getEventSelectionKey));
  state.selectedEventIds = new Set(Array.from(state.selectedEventIds || []).filter(id => validIds.has(id)));
}

function clearMessageRangeSelection(){
  state.isMessageRangeSelectionMode = false;
  state.selectedMessageRangeEventId = '';
  state.detailMessageRangeMode = '';
}

function syncSelectedMessageRangeToActiveEvents(){
  if(!state.selectedMessageRangeEventId){
    return;
  }
  if(getSelectedMessageRangeEvent()){
    return;
  }
  state.selectedMessageRangeEventId = '';
  state.detailMessageRangeMode = '';
}

function updateDisplayedMessagesCopyButtonState(){
  const button = document.getElementById('copy_displayed_messages');
  if(!state.activeSession){
    button.disabled = true;
    return;
  }
  const hasMessages = !!getDisplayMessageEvents().length;
  button.disabled = state.isDetailLoading || !hasMessages;
}

function updateCopyResumeButtonState(){
  const button = document.getElementById('copy_resume_command');
  button.disabled = !getActiveSessionId();
}

function updateEventSelectionModeButtonState(){
  const button = document.getElementById('event_selection_mode');
  if(!button){
    return;
  }
  const hasSelectableMessages = !!getSelectableDisplayMessageEvents().length;
  const hasSelectedMessages = !!getSelectedMessageEvents().length;
  button.disabled = !state.activeSession || (!hasSelectableMessages && !hasSelectedMessages && !state.isEventSelectionMode);
  button.textContent = state.isEventSelectionMode ? t('detail.selectEnd') : t('detail.selectMode');
  button.classList.toggle('selection-active', state.isEventSelectionMode);
}

function updateCopySelectedMessagesButtonState(){
  const button = document.getElementById('copy_selected_messages');
  if(!button){
    return;
  }
  const selectedMessages = getSelectedMessageEvents();
  const defaultLabel = selectedMessages.length
    ? t('detail.copySelectedCount', { count: selectedMessages.length })
    : t('detail.copySelected');
  button.disabled = state.isDetailLoading || selectedMessages.length === 0;
  button.textContent = defaultLabel;
  button.dataset.defaultLabel = defaultLabel;
}

function updateMessageRangeSelectionModeButtonState(){
  const button = document.getElementById('message_range_selection_mode');
  if(!button){
    return;
  }
  const hasSelectableMessages = !!getSelectableDisplayMessageEvents().length;
  const hasSelectedMessage = !!getSelectedMessageRangeEvent();
  button.disabled = !state.activeSession || (!hasSelectableMessages && !hasSelectedMessage && !state.isMessageRangeSelectionMode);
  button.textContent = state.isMessageRangeSelectionMode ? t('detail.rangeModeEnd') : t('detail.rangeMode');
  button.classList.toggle('selection-active', state.isMessageRangeSelectionMode);
}

function updateClearMessageRangeSelectionButtonState(){
  const button = document.getElementById('clear_message_range_selection');
  if(!button){
    return;
  }
  button.disabled = !state.activeSession || (!getSelectedMessageRangeEvent() && !state.detailMessageRangeMode);
}

function updateMessageRangeFilterButtonsState(){
  const afterButton = document.getElementById('detail_message_range_after');
  const beforeButton = document.getElementById('detail_message_range_before');
  if(!afterButton || !beforeButton){
    return;
  }
  const hasSelectedMessage = !!getSelectedMessageRangeEvent();
  const isAfterActive = state.detailMessageRangeMode === 'after';
  const isBeforeActive = state.detailMessageRangeMode === 'before';
  const hasActiveRangeMode = isAfterActive || isBeforeActive;
  afterButton.disabled = state.isDetailLoading || !hasSelectedMessage;
  beforeButton.disabled = state.isDetailLoading || !hasSelectedMessage;
  afterButton.classList.toggle('active', isAfterActive);
  beforeButton.classList.toggle('active', isBeforeActive);
  afterButton.classList.toggle('contrast-dim', hasActiveRangeMode && !isAfterActive);
  beforeButton.classList.toggle('contrast-dim', hasActiveRangeMode && !isBeforeActive);
  afterButton.textContent = isAfterActive ? t('detail.rangeAfterActive') : t('detail.rangeAfter');
  beforeButton.textContent = isBeforeActive ? t('detail.rangeBeforeActive') : t('detail.rangeBefore');
  afterButton.setAttribute('aria-pressed', isAfterActive ? 'true' : 'false');
  beforeButton.setAttribute('aria-pressed', isBeforeActive ? 'true' : 'false');
}

function updateRefreshDetailButtonState(){
  const button = document.getElementById('refresh_detail');
  const isManualRefresh = state.isDetailLoading && state.detailLoadMode === 'refresh';
  button.disabled = !state.activePath || isManualRefresh;
  if(!isManualRefresh){
    button.textContent = t('detail.refresh');
    return;
  }
  button.textContent = t('detail.refreshing');
}

function hasDetailFilter(){
  return Boolean(
    document.getElementById('only_user_instruction').checked ||
    document.getElementById('only_ai_response').checked ||
    document.getElementById('turn_boundary_only').checked ||
    document.getElementById('reverse_order').checked ||
    getSelectedDetailEventLabelFilter() ||
    state.detailMessageRangeMode ||
    getDetailKeywordInputValue() ||
    detailKeywordFilterTerm !== '' ||
    detailKeywordSearchTerm !== '' ||
    state.isEventSelectionMode ||
    ((state.selectedEventIds && state.selectedEventIds.size) || 0) > 0 ||
    state.isMessageRangeSelectionMode ||
    state.selectedMessageRangeEventId ||
    document.getElementById('detail_event_date_from_date').value ||
    document.getElementById('detail_event_date_from_time').value ||
    document.getElementById('detail_event_date_to_date').value ||
    document.getElementById('detail_event_date_to_time').value
  );
}

function hasDetailEventDateFilter(){
  return Boolean(
    document.getElementById('detail_event_date_from_date').value ||
    document.getElementById('detail_event_date_from_time').value ||
    document.getElementById('detail_event_date_to_date').value ||
    document.getElementById('detail_event_date_to_time').value
  );
}

function updateClearDetailButtonState(){
  const button = document.getElementById('clear_detail');
  if(!button){
    return;
  }
  button.disabled = !state.activeSession || !hasDetailFilter();
}

function hasListFilter(){
  return Boolean(
    document.getElementById('cwd_q').value.trim() ||
    document.getElementById('date_from').value ||
    document.getElementById('date_to').value ||
    document.getElementById('event_date_from_date').value ||
    document.getElementById('event_date_from_time').value ||
    document.getElementById('event_date_to_date').value ||
    document.getElementById('event_date_to_time').value ||
    document.getElementById('q').value.trim() ||
    normalizeSourceFilter(document.getElementById('source_filter').value || 'all') !== 'all' ||
    normalizeSubagentsFilter(document.getElementById('subagents_filter').value || 'include') !== 'include' ||
    getSelectedSessionLabelFilter() ||
    getSelectedListEventLabelFilter()
  );
}

async function copyResumeCommand(){
  const sessionId = getActiveSessionId();
  if(!sessionId) return;

  const commandText = 'claude --resume ' + sessionId;
  const copied = await copyTextToClipboard(commandText);

  if(copied){
    const button = document.getElementById('copy_resume_command');
    flashButtonLabel(button, t('copy.copied'), t('detail.copyResume'));
  }
}

function scheduleLoadSessions(){
  saveFilters();
  if(loadSessionsTimer){
    clearTimeout(loadSessionsTimer);
  }
  loadSessionsTimer = setTimeout(() => {
    loadSessionsTimer = null;
    loadSessions();
  }, SEARCH_DEBOUNCE_MS);
}

function normalizeRequestError(error, fallback){
  if(error && typeof error.message === 'string' && error.message.trim()){
    return error.message.trim();
  }
  return fallback;
}

function getActiveSortOrder(){
  const active = document.querySelector('.sort-tab.active');
  return active ? active.dataset.sort : 'desc';
}

function setActiveSortOrder(value){
  document.querySelectorAll('.sort-tab').forEach(tab => {
    const isActive = tab.dataset.sort === value;
    tab.classList.toggle('active', isActive);
    tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
  });
}

async function loadSessions(options){
  saveFilters();
  const requestId = ++loadSessionsRequestSeq;
  const loadMode = options && options.mode ? options.mode : 'auto';
  state.isSessionsLoading = true;
  state.sessionsError = '';
  state.sessionsLoadMode = loadMode;
  renderSessionList();
  const params = new URLSearchParams();
  params.set('ts', Date.now().toString());
  const q = document.getElementById('q').value.trim();
  if(q){
    params.set('q', q);
    params.set('mode', document.getElementById('mode').value);
  }
  const sessionLabelId = getSelectedSessionLabelFilter();
  const eventLabelId = getSelectedListEventLabelFilter();
  if(sessionLabelId){
    params.set('session_label_id', sessionLabelId);
  }
  if(eventLabelId){
    params.set('event_label_id', eventLabelId);
  }
  const sortOrder = getActiveSortOrder();
  if(sortOrder && sortOrder !== 'desc'){
    params.set('sort', sortOrder);
  }
  try {
    const r = await fetch('/api/sessions?' + params.toString(), { cache: 'no-store' });
    const data = await r.json();
    if(requestId !== loadSessionsRequestSeq){
      return;
    }
    state.sessions = Array.isArray(data.sessions) ? data.sessions : [];
    state.sessionsError = data.error || '';
    state.sessionRoot = data.root || '';
    applyFilter();
    if(state.activePath){
      const exists = state.sessions.some(s => s.path === state.activePath);
      if(exists){
        syncActiveSessionSummaryFromList(state.activePath);
        if(shouldSyncActiveSessionAfterListLoad(loadMode)){
          if(isAutomaticSessionsLoadMode(loadMode) && hasRecentDetailInteraction()){
            pendingAutomaticDetailSync = true;
            renderSessionList();
            renderActiveSession();
            scheduleDeferredAutomaticDetailSync();
          } else {
            pendingAutomaticDetailSync = false;
            clearDeferredDetailSyncTimer();
            await openSession(state.activePath, { mode: 'sync' });
          }
        } else {
          renderSessionList();
          renderActiveSession();
        }
      } else {
        state.activePath = null;
        state.activeSession = null;
        state.activeEvents = [];
        state.activeRawLineCount = 0;
        state.detailError = '';
        state.detailLoadMode = '';
        clearSelectedEventIds();
        clearMessageRangeSelection();
        pendingAutomaticDetailSync = false;
        clearDeferredDetailSyncTimer();
        renderSessionList();
        renderActiveSession();
      }
    }
  } catch (error) {
    if(requestId !== loadSessionsRequestSeq){
      return;
    }
    state.sessionsError = normalizeRequestError(error, t('error.sessions'));
    renderSessionList();
  } finally {
    if(requestId === loadSessionsRequestSeq){
      state.isSessionsLoading = false;
      state.hasLoadedSessions = true;
      state.sessionsLoadMode = '';
      renderSessionList();
    }
  }
}

function saveFilters(){
  const dateFromIso = parseDateInputToIso(document.getElementById('date_from').value);
  const dateToIso = parseDateInputToIso(document.getElementById('date_to').value);
  const eventDateFromDate = parseDateInputToIso(document.getElementById('event_date_from_date').value);
  const eventDateFromTime = parseTimeInputToValue(document.getElementById('event_date_from_time').value);
  const eventDateToDate = parseDateInputToIso(document.getElementById('event_date_to_date').value);
  const eventDateToTime = parseTimeInputToValue(document.getElementById('event_date_to_time').value);
  const detailEventDateFromDate = parseDateInputToIso(document.getElementById('detail_event_date_from_date').value);
  const detailEventDateFromTime = parseTimeInputToValue(document.getElementById('detail_event_date_from_time').value);
  const detailEventDateToDate = parseDateInputToIso(document.getElementById('detail_event_date_to_date').value);
  const detailEventDateToTime = parseTimeInputToValue(document.getElementById('detail_event_date_to_time').value);
  const eventDateFromIso = buildDateTimeIsoFromParts(eventDateFromDate, eventDateFromTime, 'start');
  const eventDateToIso = buildDateTimeIsoFromParts(eventDateToDate, eventDateToTime, 'end');
  const detailEventDateFromIso = buildDateTimeIsoFromParts(detailEventDateFromDate, detailEventDateFromTime, 'start');
  const detailEventDateToIso = buildDateTimeIsoFromParts(detailEventDateToDate, detailEventDateToTime, 'end');
  document.getElementById('date_from').value = dateFromIso;
  document.getElementById('date_to').value = dateToIso;
  document.getElementById('event_date_from_date').value = eventDateFromDate;
  document.getElementById('event_date_from_time').value = eventDateFromTime;
  document.getElementById('event_date_to_date').value = eventDateToDate;
  document.getElementById('event_date_to_time').value = eventDateToTime;
  document.getElementById('detail_event_date_from_date').value = detailEventDateFromDate;
  document.getElementById('detail_event_date_from_time').value = detailEventDateFromTime;
  document.getElementById('detail_event_date_to_date').value = detailEventDateToDate;
  document.getElementById('detail_event_date_to_time').value = detailEventDateToTime;
  refreshDateTimeInputPairStates();
  const payload = {
    cwd_q: document.getElementById('cwd_q').value,
    date_from: dateFromIso,
    date_to: dateToIso,
    event_date_from_date: eventDateFromDate,
    event_date_from_time: eventDateFromTime,
    event_date_to_date: eventDateToDate,
    event_date_to_time: eventDateToTime,
    q: document.getElementById('q').value,
    mode: document.getElementById('mode').value,
    source_filter: document.getElementById('source_filter').value,
    subagents_filter: document.getElementById('subagents_filter').value,
    sort_order: getActiveSortOrder(),
    session_label_filter: getSelectedSessionLabelFilter(),
    event_label_filter: getSelectedListEventLabelFilter(),
    detail_event_label_filter: getSelectedDetailEventLabelFilter(),
    filters_visible: filtersVisible,
    detail_actions_visible: detailActionsVisible,
    left_pane_visible: leftPaneVisible,
  };
  try {
    localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(payload));
  } catch (e) {
    // Ignore storage write errors.
  }
}

function restoreFilters(){
  let raw = null;
  try {
    raw = localStorage.getItem(FILTER_STORAGE_KEY);
  } catch (e) {
    raw = null;
  }
  if(!raw) return;
  try {
    const data = JSON.parse(raw);
    if(typeof data.cwd_q === 'string') document.getElementById('cwd_q').value = data.cwd_q;
    if(typeof data.date_from === 'string') document.getElementById('date_from').value = parseDateInputToIso(data.date_from);
    if(typeof data.date_to === 'string') document.getElementById('date_to').value = parseDateInputToIso(data.date_to);
    if(typeof data.event_date_from_date === 'string' || typeof data.event_date_from_time === 'string'){
      document.getElementById('event_date_from_date').value = parseDateInputToIso(data.event_date_from_date);
      document.getElementById('event_date_from_time').value = parseTimeInputToValue(data.event_date_from_time);
    } else if(typeof data.event_date_from === 'string'){
      setDateTimePairFromIso('event_date_from_date', 'event_date_from_time', data.event_date_from);
    }
    if(typeof data.event_date_to_date === 'string' || typeof data.event_date_to_time === 'string'){
      document.getElementById('event_date_to_date').value = parseDateInputToIso(data.event_date_to_date);
      document.getElementById('event_date_to_time').value = parseTimeInputToValue(data.event_date_to_time);
    } else if(typeof data.event_date_to === 'string'){
      setDateTimePairFromIso('event_date_to_date', 'event_date_to_time', data.event_date_to);
    }
    if(typeof data.q === 'string') document.getElementById('q').value = data.q;
    if(data.mode === 'and' || data.mode === 'or') document.getElementById('mode').value = data.mode;
    const source = normalizeSourceFilter(data.source_filter || 'all');
    document.getElementById('source_filter').value = source;
    document.getElementById('subagents_filter').value = normalizeSubagentsFilter(data.subagents_filter || 'include');
    if(data.sort_order === 'asc' || data.sort_order === 'desc' || data.sort_order === 'updated') setActiveSortOrder(data.sort_order);
    if(typeof data.session_label_filter === 'string') document.getElementById('session_label_filter').dataset.pendingValue = data.session_label_filter;
    if(typeof data.event_label_filter === 'string') document.getElementById('event_label_filter').dataset.pendingValue = data.event_label_filter;
    if(typeof data.detail_event_label_filter === 'string') document.getElementById('detail_event_label_filter').dataset.pendingValue = data.detail_event_label_filter;
    refreshDateTimeInputPairStates();
    if(typeof data.filters_visible === 'boolean') filtersVisible = data.filters_visible;
    if(typeof data.detail_actions_visible === 'boolean') detailActionsVisible = data.detail_actions_visible;
    if(typeof data.left_pane_visible === 'boolean') leftPaneVisible = data.left_pane_visible;
  } catch (e) {
    // Ignore invalid saved filters.
  }
}

function clearFilters(){
  cancelScheduledSaveFilters();
  document.getElementById('cwd_q').value = '';
  document.getElementById('date_from').value = '';
  document.getElementById('date_to').value = '';
  document.getElementById('event_date_from_date').value = '';
  document.getElementById('event_date_from_time').value = '';
  document.getElementById('event_date_to_date').value = '';
  document.getElementById('event_date_to_time').value = '';
  document.getElementById('q').value = '';
  document.getElementById('mode').value = 'and';
  document.getElementById('source_filter').value = 'all';
  document.getElementById('subagents_filter').value = 'include';
  setActiveSortOrder('desc');
  document.getElementById('session_label_filter').value = '';
  document.getElementById('event_label_filter').value = '';
  document.getElementById('detail_event_label_filter').value = '';
  refreshDateTimeInputPairStates();
  saveFilters();
  if(loadSessionsTimer){
    clearTimeout(loadSessionsTimer);
    loadSessionsTimer = null;
  }
  loadSessions({ mode: 'clear' });
}

function applyFilter(){
  const cwdQ = document.getElementById('cwd_q').value.toLowerCase().trim();
  const sourceFilter = normalizeSourceFilter(document.getElementById('source_filter').value || 'all');
  const subagentsFilter = normalizeSubagentsFilter(document.getElementById('subagents_filter').value || 'include');
  const fromRaw = document.getElementById('date_from').value;
  const toRaw = document.getElementById('date_to').value;
  const fromTs = parseOptionalDateStart(fromRaw);
  const toTs = parseOptionalDateEnd(toRaw);
  const evFromRaw = buildDateTimeIsoFromParts(
    document.getElementById('event_date_from_date').value,
    document.getElementById('event_date_from_time').value,
    'start'
  );
  const evToRaw = buildDateTimeIsoFromParts(
    document.getElementById('event_date_to_date').value,
    document.getElementById('event_date_to_time').value,
    'end'
  );
  const evFromTs = parseOptionalDatetimeStart(evFromRaw);
  const evToTs = parseOptionalDatetimeEnd(evToRaw);
  state.filtered = state.sessions.filter(s => {
    const cwdMatched = !cwdQ || (s.cwd || '').toLowerCase().includes(cwdQ);
    const sourceMatched = sourceFilter === 'all' || normalizeSourceFilter(s.source_type || s.source) === sourceFilter;
    const isSubagents = hasSubagentsSegment(s);
    const subagentsMatched = subagentsFilter === 'exclude' ? !isSubagents : true;

    let dateMatched = true;
    if(fromTs !== null || toTs !== null){
      const sessionTs = toTimestamp(s.started_at || s.mtime);
      if(Number.isNaN(sessionTs)){
        dateMatched = false;
      } else {
        if(fromTs !== null && sessionTs < fromTs){
          dateMatched = false;
        }
        if(toTs !== null && sessionTs > toTs){
          dateMatched = false;
        }
      }
    }

    let eventDateMatched = true;
    if(evFromTs !== null || evToTs !== null){
      const minTs = s.min_event_ts ? toTimestamp(s.min_event_ts) : NaN;
      const maxTs = s.max_event_ts ? toTimestamp(s.max_event_ts) : NaN;
      if(Number.isNaN(minTs) || Number.isNaN(maxTs)){
        eventDateMatched = false;
      } else {
        if(evFromTs !== null && maxTs < evFromTs){
          eventDateMatched = false;
        }
        if(evToTs !== null && minTs > evToTs){
          eventDateMatched = false;
        }
      }
    }

    return cwdMatched && sourceMatched && subagentsMatched && dateMatched && eventDateMatched;
  });
  saveFilters();
  renderSessionList();
}

function renderSessionList(){
  const box = document.getElementById('sessions');
  updateReloadButtonState();
  if(state.isSessionsLoading && !state.hasLoadedSessions){
    box.innerHTML = renderInlineStatus(
      t('status.sessions.loadingTitle'),
      t('status.sessions.loadingCopy'),
      'loading'
    );
  } else if(state.sessionsError && !state.sessions.length){
    box.innerHTML = renderInlineStatus(
      t('status.sessions.errorTitle'),
      state.sessionsError,
      'error'
    );
  } else if(!state.filtered.length){
    box.innerHTML = hasListFilter()
      ? renderInlineStatus(
          t('status.sessions.noMatchesTitle'),
          t('status.sessions.noMatchesCopy'),
          'empty'
        )
      : renderInlineStatus(
          t('status.sessions.emptyTitle'),
          t('status.sessions.emptyCopy'),
          'empty'
        );
  } else {
    box.innerHTML = state.filtered.map(s => `
      <div class="session-item ${state.activePath === s.path ? 'active' : ''}" data-path="${esc(s.path)}">
        <div class="session-meta-row session-meta-row-secondary">
          <div class="session-badge session-cwd">${esc(s.cwd || '-')}</div>
        </div>
        <div class="session-meta-row session-meta-row-primary">
          <div class="session-badge session-time">${esc(fmt(s.started_at || s.mtime))}</div>
          <div class="session-badge session-source source-${esc(normalizeSource(s.source_type || s.source))}">${esc(sourceLabel(s.source_type || s.source))}</div>
          ${hasSubagentsSegment(s) ? '<div class="session-badge session-tag-subagents">subagents</div>' : ''}
        </div>
        <div class="session-preview">${esc(s.first_real_user_text || s.first_user_text || t('session.preview.empty'))}</div>
        ${(s.session_labels || []).length ? `<div class="session-label-row">${renderAssignedLabels(s.session_labels || [])}</div>` : ''}
      </div>
    `).join('');
  }
  if(state.isSessionsLoading && state.hasLoadedSessions && (state.sessionsLoadMode === 'reload' || state.sessionsLoadMode === 'auto' || state.sessionsLoadMode === 'clear')){
    setStatusLayer(
      'sessions_status',
      t('status.sessions.refreshTitle'),
      t('status.sessions.refreshCopy'),
      'loading'
    );
  } else {
    setStatusLayer('sessions_status');
  }
  box.querySelectorAll('.session-item').forEach(el => {
    el.onclick = () => openSession(el.dataset.path);
  });
  const countEl = document.getElementById('session_count');
  if(countEl){
    if(state.hasLoadedSessions && state.sessions.length > 0){
      const currentIndex = state.activePath ? state.filtered.findIndex(s => s.path === state.activePath) : -1;
      const currentLabel = currentIndex >= 0 ? String(currentIndex + 1) : '-';
      countEl.textContent = t('summary.sessions', { current: currentLabel, filtered: state.filtered.length, total: state.sessions.length });
    } else {
      countEl.textContent = '';
    }
  }
}

function getDisplayEvents(){
  let events = state.activeEvents || [];
  if(isTurnBoundaryFilterEnabled()){
    events = filterEventsToTurnBoundaries(events);
  }
  const selectedEventLabelId = getSelectedDetailEventLabelFilter();
  if(selectedEventLabelId){
    events = events.filter(ev => (ev.labels || []).some(label => String(label.id) === selectedEventLabelId));
  }
  const showOnlyUser = document.getElementById('only_user_instruction').checked;
  const showOnlyAssistant = document.getElementById('only_ai_response').checked;
  if(showOnlyUser || showOnlyAssistant){
    events = events.filter(ev => {
      if(ev.kind !== 'message') return false;
      if(showOnlyUser && ev.role === 'user'){
        if(isSystemLabeledUserEvent(ev)){
          return false;
        }
        return true;
      }
      if(showOnlyAssistant && ev.role === 'assistant'){
        return true;
      }
      return false;
    });
  }
  if(state.detailMessageRangeMode){
    const selectedMessage = getSelectedMessageRangeEvent();
    if(selectedMessage){
      const activeEvents = state.activeEvents || [];
      const selectedIndex = activeEvents.findIndex(ev => ev === selectedMessage);
      const rawIndexByEvent = new Map(activeEvents.map((ev, index) => [ev, index]));
      if(selectedIndex >= 0){
        events = events.filter(ev => {
          if(ev.kind !== 'message'){
            return false;
          }
          const rawIndex = rawIndexByEvent.get(ev);
          if(typeof rawIndex !== 'number'){
            return false;
          }
          if(state.detailMessageRangeMode === 'after'){
            return rawIndex >= selectedIndex;
          }
          if(state.detailMessageRangeMode === 'before'){
            return rawIndex <= selectedIndex;
          }
          return true;
        });
      }
    }
  }
  if(detailKeywordFilterTerm !== ''){
    events = events.filter(ev => containsLiteralKeyword(getEventBodyText(ev), detailKeywordFilterTerm));
  }
  const detailEvFromRaw = buildDateTimeIsoFromParts(
    document.getElementById('detail_event_date_from_date').value,
    document.getElementById('detail_event_date_from_time').value,
    'start'
  );
  const detailEvToRaw = buildDateTimeIsoFromParts(
    document.getElementById('detail_event_date_to_date').value,
    document.getElementById('detail_event_date_to_time').value,
    'end'
  );
  const detailEvFromTs = parseOptionalDatetimeStart(detailEvFromRaw);
  const detailEvToTs = parseOptionalDatetimeEnd(detailEvToRaw);
  if(detailEvFromTs !== null || detailEvToTs !== null){
    events = events.filter(ev => {
      const evTs = ev.timestamp ? toTimestamp(ev.timestamp) : NaN;
      if(Number.isNaN(evTs)) return false;
      if(detailEvFromTs !== null && evTs < detailEvFromTs) return false;
      if(detailEvToTs !== null && evTs > detailEvToTs) return false;
      return true;
    });
  }
  if(document.getElementById('reverse_order').checked){
    events = [...events].reverse();
  }
  return events;
}

function formatCopiedMessages(events){
  return events.map(ev => {
    const role = ev.role || 'system';
    const timestamp = fmt(ev.timestamp) || ev.timestamp || '-';
    return `[${role}] ${timestamp}\n${ev.text || ''}`;
  }).join('\\n\\n-----\\n\\n');
}

async function removeSessionLabel(labelId){
  if(!state.activePath) return;
  const data = await postJson('/api/session-label/remove', {
    path: state.activePath,
    label_id: labelId,
  });
  if(data.error){
    alert(data.error);
    return;
  }
  await loadSessions({ mode: 'labels' });
}

async function addSessionLabelFromButton(button){
  if(!state.activePath) return;
  showLabelPicker(button, async (labelId) => {
    const data = await postJson('/api/session-label/add', {
      path: state.activePath,
      label_id: labelId,
    });
    if(data.error){
      alert(data.error);
      return;
    }
    await loadSessions({ mode: 'labels' });
  });
}

async function addEventLabelFromButton(button, eventId){
  if(!state.activePath || !eventId) return;
  showLabelPicker(button, async (labelId) => {
    const data = await postJson('/api/event-label/add', {
      path: state.activePath,
      event_id: eventId,
      label_id: labelId,
    });
    if(data.error){
      alert(data.error);
      return;
    }
    await loadSessions({ mode: 'labels' });
  });
}

async function removeEventLabel(eventId, labelId){
  if(!state.activePath || !eventId) return;
  const data = await postJson('/api/event-label/remove', {
    path: state.activePath,
    event_id: eventId,
    label_id: labelId,
  });
  if(data.error){
    alert(data.error);
    return;
  }
  await loadSessions({ mode: 'labels' });
}

async function copyDisplayedMessages(){
  const messages = getDisplayMessageEvents();
  if(!messages.length){
    return;
  }
  const copied = await copyTextToClipboard(formatCopiedMessages(messages));
  if(copied){
    const button = document.getElementById('copy_displayed_messages');
    flashButtonLabel(button, t('copy.displayedCount', { count: messages.length }), t('detail.copyDisplayed'));
  }
}

async function copySelectedMessages(){
  const messages = getSelectedMessageEvents();
  if(!messages.length){
    return;
  }
  const copied = await copyTextToClipboard(formatCopiedMessages(messages));
  if(copied){
    const copiedCount = messages.length;
    const button = document.getElementById('copy_selected_messages');
    flashButtonLabel(button, t('copy.selectedCount', { count: copiedCount }), t('detail.copySelected'), BUTTON_FEEDBACK_MS);
    await waitForUiFeedback(BUTTON_FEEDBACK_MS);
    state.isEventSelectionMode = false;
    clearSelectedEventIds();
    renderActiveSession();
  }
}

async function copyEventMessage(button, eventId){
  const event = (state.activeEvents || []).find(ev => ev.event_id === eventId && ev.kind === 'message');
  if(!event || !event.text){
    return;
  }
  const copied = await copyTextToClipboard(event.text);
  if(copied){
    flashButtonLabel(button, t('copy.copied'), t('copy.single'));
  }
}

function toggleEventSelectionMode(){
  const nextEnabled = !state.isEventSelectionMode;
  state.isEventSelectionMode = nextEnabled;
  if(nextEnabled){
    state.isMessageRangeSelectionMode = false;
  } else {
    clearSelectedEventIds();
  }
  renderActiveSession();
}

function updateEventSelection(eventId, checked, card){
  const key = String(eventId || '');
  if(!key){
    return;
  }
  if(checked){
    state.selectedEventIds.add(key);
  } else {
    state.selectedEventIds.delete(key);
  }
  if(card){
    card.classList.toggle('copy-selected', checked);
  }
  updateCopySelectedMessagesButtonState();
  updateClearDetailButtonState();
}

function toggleMessageRangeSelectionMode(){
  const nextEnabled = !state.isMessageRangeSelectionMode;
  state.isMessageRangeSelectionMode = nextEnabled;
  if(nextEnabled){
    state.isEventSelectionMode = false;
    clearSelectedEventIds();
  }
  renderActiveSession();
}

function updateMessageRangeSelection(eventId){
  const key = String(eventId || '');
  if(!key){
    return;
  }
  noteDetailInteraction();
  state.selectedMessageRangeEventId = key;
  renderActiveSession();
}

function applyDetailMessageRange(mode){
  if(!getSelectedMessageRangeEvent()){
    return;
  }
  noteDetailInteraction();
  state.detailMessageRangeMode = mode === 'before' ? 'before' : 'after';
  const eventsBox = document.getElementById('events');
  if(eventsBox){
    eventsBox.scrollTop = 0;
  }
  renderActiveSession();
}

function clearDetailMessageRangeSelection(){
  noteDetailInteraction();
  clearMessageRangeSelection();
  renderActiveSession();
}

function applyDetailKeywordFilter(){
  noteDetailInteraction();
  const inputValue = getDetailKeywordInputValue();
  if(detailKeywordFilterTerm !== '' && inputValue === detailKeywordFilterTerm){
    detailKeywordFilterTerm = '';
  } else {
    detailKeywordFilterTerm = inputValue;
  }
  const eventsBox = document.getElementById('events');
  if(eventsBox){
    eventsBox.scrollTop = 0;
  }
  renderActiveSession();
}

function runDetailKeywordSearch(){
  noteDetailInteraction();
  detailKeywordSearchTerm = getDetailKeywordInputValue();
  const searchMeta = buildDetailKeywordSearchMeta(getDisplayEvents(), detailKeywordSearchTerm);
  detailKeywordSearchTotal = searchMeta.total;
  detailKeywordCurrentMatchIndex = searchMeta.total ? 0 : -1;
  pendingDetailKeywordFocusIndex = detailKeywordCurrentMatchIndex;
  renderActiveSession();
}

function moveDetailKeywordSearch(step){
  noteDetailInteraction();
  const searchMeta = buildDetailKeywordSearchMeta(getDisplayEvents(), detailKeywordSearchTerm);
  detailKeywordSearchTotal = searchMeta.total;
  if(!searchMeta.total){
    detailKeywordCurrentMatchIndex = -1;
    pendingDetailKeywordFocusIndex = -1;
    renderActiveSession();
    return;
  }
  if(detailKeywordCurrentMatchIndex < 0 || detailKeywordCurrentMatchIndex >= searchMeta.total){
    detailKeywordCurrentMatchIndex = 0;
  } else {
    detailKeywordCurrentMatchIndex = (detailKeywordCurrentMatchIndex + step + searchMeta.total) % searchMeta.total;
  }
  pendingDetailKeywordFocusIndex = detailKeywordCurrentMatchIndex;
  renderActiveSession();
}

function clearDetailKeyword(){
  noteDetailInteraction();
  const input = document.getElementById('detail_keyword_q');
  if(input){
    input.value = '';
  }
  resetDetailKeywordState();
  renderActiveSession();
}

function clearDetailFilters(){
  noteDetailInteraction();
  document.getElementById('only_user_instruction').checked = false;
  document.getElementById('only_ai_response').checked = false;
  document.getElementById('turn_boundary_only').checked = false;
  document.getElementById('reverse_order').checked = false;
  const detailEventLabelFilter = document.getElementById('detail_event_label_filter');
  detailEventLabelFilter.value = '';
  delete detailEventLabelFilter.dataset.pendingValue;
  const detailKeywordInput = document.getElementById('detail_keyword_q');
  if(detailKeywordInput){
    detailKeywordInput.value = '';
  }
  document.getElementById('detail_event_date_from_date').value = '';
  document.getElementById('detail_event_date_from_time').value = '';
  document.getElementById('detail_event_date_to_date').value = '';
  document.getElementById('detail_event_date_to_time').value = '';
  refreshDateTimeInputPairStates();
  resetDetailKeywordState();
  state.isEventSelectionMode = false;
  clearSelectedEventIds();
  clearMessageRangeSelection();
  hideLabelPicker();
  saveFilters();
  renderActiveSession();
}

function renderActiveSession(){
  const meta = document.getElementById('meta');
  const eventsBox = document.getElementById('events');
  updateRefreshDetailButtonState();
  updateDetailDisplayControlsState();
  const sessionRootRow = state.sessionRoot
    ? `<div class="header-meta-row">
      <span class="header-meta-label">${esc(t('meta.sessionRoot'))}</span>
      <span class="header-meta-value">${esc(state.sessionRoot)}</span>
    </div>`
    : '';
  if(!state.activeSession){
    detailKeywordSearchTotal = 0;
    normalizeDetailKeywordSearchPosition({ total: 0 });
    if(state.isDetailLoading && state.activePath){
      meta.innerHTML = `${sessionRootRow}<div class="header-meta-row"><span class="header-meta-text">${esc(t('status.detail.loadingTitle'))}</span></div>`;
      eventsBox.innerHTML = renderInlineStatus(
        t('status.detail.loadingTitle'),
        t('status.detail.loadingCopy'),
        'loading'
      );
    } else if(state.detailError){
      meta.innerHTML = `${sessionRootRow}<div class="header-meta-row"><span class="header-meta-text error">${esc(state.detailError)}</span></div>`;
      eventsBox.innerHTML = renderInlineStatus(
        t('status.detail.errorTitle'),
        state.detailError,
        'error'
      );
    } else {
      meta.innerHTML = `${sessionRootRow}<div class="header-meta-row"><span class="header-meta-text">${esc(t('status.detail.selectSession'))}</span></div>`;
      eventsBox.innerHTML = '';
    }
    updateDetailMetaVisibility();
    setStatusLayer('detail_status');
    updateCopyResumeButtonState();
    updateDisplayedMessagesCopyButtonState();
    updateEventSelectionModeButtonState();
    updateCopySelectedMessagesButtonState();
    updateMessageRangeSelectionModeButtonState();
    updateClearMessageRangeSelectionButtonState();
    updateMessageRangeFilterButtonsState();
    updateDetailKeywordControls({ total: 0 });
    renderSessionLabelStrip();
    updateSessionLabelButtonState();
    return;
  }

  syncSelectedEventIdsToActiveEvents();
  syncSelectedMessageRangeToActiveEvents();
  const displayEvents = getDisplayEvents();
  const searchMeta = buildDetailKeywordSearchMeta(displayEvents, detailKeywordSearchTerm);
  detailKeywordSearchTotal = searchMeta.total;
  normalizeDetailKeywordSearchPosition(searchMeta);
  const sourceType = state.activeSession.source_type || state.activeSession.source || '';
  const source = normalizeSource(sourceType);
  const eventsSummary = state.isDetailLoading && state.activeEvents.length === 0
    ? t('summary.eventsLoading')
    : t('summary.events', { visible: displayEvents.length, total: state.activeEvents.length });
  const rawSummary = t('summary.raw', {
    count: state.isDetailLoading && state.activeEvents.length === 0 ? '...' : state.activeRawLineCount,
  });
  const errorNote = state.detailError
    ? `<span class="header-meta-text error">${esc(t('meta.status'))}: ${esc(state.detailError)}</span>`
    : '';
  meta.innerHTML = `
    ${sessionRootRow}
    <div class="header-meta-row">
      <span class="header-meta-label">${esc(t('meta.path'))}</span>
      <span class="header-meta-value">${highlightSessionPath(state.activeSession.relative_path)}</span>
    </div>
    <div class="header-meta-row">
      <span class="header-meta-label">${esc(t('meta.cwd'))}</span>
      <span class="header-meta-value">${esc(state.activeSession.cwd || '-')}</span>
    </div>
    <div class="header-meta-row">
      <span class="header-meta-label">${esc(t('meta.time'))}</span>
      <span class="header-meta-value">${esc(fmt(state.activeSession.started_at || state.activeSession.mtime))}</span>
      <span class="meta-tag source-${esc(source)}">${esc(sourceLabel(sourceType))}</span>
      <span class="header-meta-text">${esc(eventsSummary)}</span>
      <span class="header-meta-text">${esc(rawSummary)}</span>
      ${errorNote}
    </div>`;
  updateDetailMetaVisibility();

  if(state.isDetailLoading && state.activeEvents.length === 0){
    eventsBox.innerHTML = renderInlineStatus(
      t('status.detail.loadingTitle'),
      t('status.detail.loadingCopy'),
      'loading'
    );
  } else if(state.detailError && state.activeEvents.length === 0){
    eventsBox.innerHTML = renderInlineStatus(
      t('status.detail.errorTitle'),
      state.detailError,
      'error'
    );
  } else if(displayEvents.length === 0){
    eventsBox.innerHTML = state.activeEvents.length === 0
      ? renderInlineStatus(
          t('status.detail.noDisplayTitle'),
          t('status.detail.noDisplayCopy'),
          'empty'
        )
      : renderInlineStatus(
          t('status.detail.noMatchTitle'),
          t('status.detail.noMatchCopy'),
          'empty'
        );
  } else {
    renderEventList(eventsBox, displayEvents, getSelectedDetailEventLabelFilter(), searchMeta);
  }
  if(state.isDetailLoading && state.activeEvents.length > 0 && state.detailLoadMode === 'refresh'){
    setStatusLayer(
      'detail_status',
      t('status.detail.refreshTitle'),
      t('status.detail.refreshCopy'),
      'loading'
    );
  } else {
    setStatusLayer('detail_status');
  }
  renderSessionLabelStrip();
  updateSessionLabelButtonState();
  updateDisplayedMessagesCopyButtonState();
  updateEventSelectionModeButtonState();
  updateCopySelectedMessagesButtonState();
  updateMessageRangeSelectionModeButtonState();
  updateClearMessageRangeSelectionButtonState();
  updateMessageRangeFilterButtonsState();
  updateDetailKeywordControls(searchMeta);
  updateCopyResumeButtonState();
}

async function openSession(path, options){
  const requestId = ++loadSessionDetailRequestSeq;
  const nextSession = state.sessions.find(s => s.path === path) || null;
  const previousPath = state.activeSession && state.activeSession.path ? state.activeSession.path : state.activePath;
  const loadMode = options && options.mode ? options.mode : 'open';
  if(loadMode !== 'sync'){
    pendingAutomaticDetailSync = false;
    clearDeferredDetailSyncTimer();
  }
  state.activePath = path;
  state.isDetailLoading = true;
  state.detailError = '';
  state.detailLoadMode = loadMode;
  if(nextSession){
    state.activeSession = nextSession;
  }
  if(!state.activeSession || state.activeSession.path !== path){
    state.activeSession = nextSession;
  }
  if(previousPath !== path){
    state.activeEvents = [];
    state.activeRawLineCount = 0;
    clearSelectedEventIds();
    clearMessageRangeSelection();
  }
  renderSessionList();
  renderActiveSession();
  try {
    const r = await fetch('/api/session?path=' + encodeURIComponent(path) + '&ts=' + Date.now(), { cache: 'no-store' });
    const data = await r.json();
    if(requestId !== loadSessionDetailRequestSeq){
      return;
    }
    if(data.error){
      state.detailError = data.error;
      if(!state.activeEvents.length){
        state.activeRawLineCount = 0;
      }
      return;
    }
    state.activeSession = data.session || nextSession;
    state.activeEvents = data.events || [];
    state.activeRawLineCount = data.raw_line_count || 0;
    state.detailError = '';
    syncSelectedEventIdsToActiveEvents();
    syncSelectedMessageRangeToActiveEvents();
  } catch (error) {
    if(requestId !== loadSessionDetailRequestSeq){
      return;
    }
    state.detailError = normalizeRequestError(error, t('error.detail'));
  } finally {
    if(requestId === loadSessionDetailRequestSeq){
      state.isDetailLoading = false;
      state.detailLoadMode = '';
      renderActiveSession();
    }
  }
}

async function refreshActiveSession(){
  if(!state.activePath) return;
  await openSession(state.activePath, { mode: 'refresh' });
}

function isEditableTarget(target){
  if(!target || !(target instanceof Element)){
    return false;
  }
  if(target.closest('input, select, textarea, [contenteditable="true"]')){
    return true;
  }
  return false;
}

function focusShortcutSearch(){
  if(state.activeSession){
    if(!detailActionsVisible){
      setDetailActionsVisible(true);
    }
    const input = document.getElementById('detail_keyword_q');
    if(input && !input.disabled){
      input.focus();
      input.select();
      return;
    }
  }
  const input = document.getElementById('q');
  if(input){
    input.focus();
    input.select();
  }
}

function isShortcutDialogOpen(){
  const dialog = document.getElementById('shortcut_dialog');
  return !!dialog && !dialog.classList.contains('hidden');
}

function openShortcutDialog(){
  hideLabelPicker();
  const dialog = document.getElementById('shortcut_dialog');
  if(!dialog){
    return;
  }
  dialog.classList.remove('hidden');
  const closeButton = document.getElementById('close_shortcuts');
  if(closeButton){
    closeButton.focus();
  }
}

function closeShortcutDialog(){
  const dialog = document.getElementById('shortcut_dialog');
  if(!dialog){
    return;
  }
  const hadDialogFocus = dialog.contains(document.activeElement);
  dialog.classList.add('hidden');
  if(hadDialogFocus){
    const trigger = document.getElementById('open_shortcuts');
    if(trigger){
      trigger.focus();
    }
  }
}

function releaseSearchFocus(){
  const active = document.activeElement;
  if(!(active instanceof HTMLElement)){
    return false;
  }
  if(active.id === 'q' || active.id === 'cwd_q' || active.id === 'detail_keyword_q'){
    active.blur();
    return true;
  }
  return false;
}

function handleShortcutEscape(){
  let handled = false;
  if(isShortcutDialogOpen()){
    closeShortcutDialog();
    handled = true;
  }
  const picker = document.getElementById('label_picker');
  if(picker && !picker.classList.contains('hidden')){
    hideLabelPicker();
    handled = true;
  }
  if(releaseSearchFocus()){
    handled = true;
  }
  return handled;
}

function openRelativeSession(step){
  if(!Array.isArray(state.filtered) || state.filtered.length === 0){
    return false;
  }
  const currentIndex = state.filtered.findIndex(session => session.path === state.activePath);
  let nextIndex = currentIndex + step;
  if(currentIndex < 0){
    nextIndex = step > 0 ? 0 : state.filtered.length - 1;
  }
  if(nextIndex < 0 || nextIndex >= state.filtered.length){
    return false;
  }
  const nextSession = state.filtered[nextIndex];
  if(!nextSession || !nextSession.path || nextSession.path === state.activePath){
    return false;
  }
  openSession(nextSession.path, { mode: 'open' });
  const activeEl = document.querySelector('#sessions .session-item.active');
  if(activeEl){
    activeEl.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
  return true;
}

function triggerButtonShortcut(id){
  const button = document.getElementById(id);
  if(!(button instanceof HTMLButtonElement) || button.disabled){
    return false;
  }
  button.click();
  return true;
}

function triggerCheckboxShortcut(id){
  const checkbox = document.getElementById(id);
  if(!(checkbox instanceof HTMLInputElement) || checkbox.disabled || checkbox.type !== 'checkbox'){
    return false;
  }
  checkbox.click();
  return true;
}

function triggerViewerRefresh(){
  if(state.activePath){
    refreshActiveSession();
    return;
  }
  if(loadSessionsTimer){
    clearTimeout(loadSessionsTimer);
    loadSessionsTimer = null;
  }
  loadSessions({ mode: 'reload' });
}

function moveDetailKeywordSearchByShortcut(step){
  if(!state.activeSession){
    return false;
  }
  const term = getDetailKeywordInputValue().trim();
  const previousTerm = detailKeywordSearchTerm;
  if(!term && !previousTerm){
    return false;
  }
  noteDetailInteraction();
  detailKeywordSearchTerm = term || previousTerm;
  const searchMeta = buildDetailKeywordSearchMeta(getDisplayEvents(), detailKeywordSearchTerm);
  detailKeywordSearchTotal = searchMeta.total;
  if(!searchMeta.total){
    detailKeywordCurrentMatchIndex = -1;
    pendingDetailKeywordFocusIndex = -1;
    renderActiveSession();
    return true;
  }
  if(previousTerm !== detailKeywordSearchTerm || detailKeywordCurrentMatchIndex < 0 || detailKeywordCurrentMatchIndex >= searchMeta.total){
    detailKeywordCurrentMatchIndex = step > 0 ? 0 : searchMeta.total - 1;
  } else {
    detailKeywordCurrentMatchIndex = (detailKeywordCurrentMatchIndex + step + searchMeta.total) % searchMeta.total;
  }
  pendingDetailKeywordFocusIndex = detailKeywordCurrentMatchIndex;
  renderActiveSession();
  return true;
}

function safeBindById(id, eventName, handler){
  const node = document.getElementById(id);
  if(!node){
    return;
  }
  node.addEventListener(eventName, handler);
}

function bindDateTimePairChange(dateId, timeId, handler){
  const run = () => {
    syncDateTimeInputPairState(dateId, timeId);
    handler();
  };
  safeBindById(dateId, 'change', run);
  safeBindById(timeId, 'change', run);
}

function bindDatePaste(id){
  const input = document.getElementById(id);
  if(!input || input.dataset.datePasteReady === '1'){
    return;
  }
  input.dataset.datePasteReady = '1';
  input.addEventListener('paste', (event) => {
    const text = event.clipboardData ? event.clipboardData.getData('text') : '';
    if(text && applyDatePasteValue(input, text)){
      event.preventDefault();
      return;
    }
    setTimeout(() => {
      applyDatePasteValue(input, input.value || '');
    }, 0);
  });
}

function bindDateTimePairPaste(dateId, timeId){
  const dateInput = document.getElementById(dateId);
  const timeInput = document.getElementById(timeId);
  if(!dateInput || !timeInput){
    return;
  }
  const bindPaste = (input) => {
    if(!input || input.dataset.dateTimePasteReady === '1'){
      return;
    }
    input.dataset.dateTimePasteReady = '1';
    input.addEventListener('paste', (event) => {
      const text = event.clipboardData ? event.clipboardData.getData('text') : '';
      if(text && applyDateTimePairPasteValue(dateInput, timeInput, input, text)){
        event.preventDefault();
        return;
      }
      setTimeout(() => {
        applyDateTimePairPasteValue(dateInput, timeInput, input, input.value || '');
      }, 0);
    });
  };
  bindPaste(dateInput);
  bindPaste(timeInput);
}

safeBindById('cwd_q', 'input', applyFilter);
safeBindById('date_from', 'change', applyFilter);
safeBindById('date_to', 'change', applyFilter);
bindDateTimePairChange('event_date_from_date', 'event_date_from_time', applyFilter);
bindDateTimePairChange('event_date_to_date', 'event_date_to_time', applyFilter);
bindDatePaste('date_from');
bindDatePaste('date_to');
bindDateTimePairPaste('event_date_from_date', 'event_date_from_time');
bindDateTimePairPaste('event_date_to_date', 'event_date_to_time');
safeBindById('q', 'input', scheduleLoadSessions);
safeBindById('mode', 'change', scheduleLoadSessions);
safeBindById('source_filter', 'change', applyFilter);
safeBindById('subagents_filter', 'change', applyFilter);
document.querySelectorAll('.sort-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    setActiveSortOrder(tab.dataset.sort);
    scheduleLoadSessions();
  });
});
safeBindById('session_label_filter', 'change', scheduleLoadSessions);
safeBindById('event_label_filter', 'change', scheduleLoadSessions);
safeBindById('detail_event_label_filter', 'change', () => {
  saveFilters();
  renderActiveSession();
});
bindDateTimePairChange('detail_event_date_from_date', 'detail_event_date_from_time', () => {
  saveFilters();
  renderActiveSession();
});
bindDateTimePairChange('detail_event_date_to_date', 'detail_event_date_to_time', () => {
  saveFilters();
  renderActiveSession();
});
bindDateTimePairPaste('detail_event_date_from_date', 'detail_event_date_from_time');
bindDateTimePairPaste('detail_event_date_to_date', 'detail_event_date_to_time');
safeBindById('clear_detail_event_date', 'click', () => {
  document.getElementById('detail_event_date_from_date').value = '';
  document.getElementById('detail_event_date_from_time').value = '';
  document.getElementById('detail_event_date_to_date').value = '';
  document.getElementById('detail_event_date_to_time').value = '';
  refreshDateTimeInputPairStates();
  saveFilters();
  renderActiveSession();
});
safeBindById('toggle_filters', 'click', () => {
  setFiltersVisible(!filtersVisible);
});
safeBindById('toggle_session_list_mobile', 'click', () => {
  setLeftPaneVisible(!leftPaneVisible);
});
safeBindById('toggle_detail_actions', 'click', () => {
  setDetailActionsVisible(!detailActionsVisible);
});
safeBindById('open_shortcuts', 'click', openShortcutDialog);
safeBindById('close_shortcuts', 'click', closeShortcutDialog);
safeBindById('toggle_meta', 'click', () => {
  setDetailMetaVisible(!detailMetaVisible);
});
safeBindById('reload', 'click', () => {
  if(loadSessionsTimer){
    clearTimeout(loadSessionsTimer);
    loadSessionsTimer = null;
  }
  loadSessions({ mode: 'reload' });
});
safeBindById('clear', 'click', clearFilters);
safeBindById('only_user_instruction', 'change', () => {
  renderActiveSession();
});
safeBindById('only_ai_response', 'change', () => {
  renderActiveSession();
});
safeBindById('turn_boundary_only', 'change', () => {
  renderActiveSession();
});
safeBindById('reverse_order', 'change', () => {
  renderActiveSession();
});
safeBindById('clear_detail', 'click', clearDetailFilters);
safeBindById('refresh_detail', 'click', refreshActiveSession);
safeBindById('copy_resume_command', 'click', copyResumeCommand);
safeBindById('copy_displayed_messages', 'click', copyDisplayedMessages);
safeBindById('event_selection_mode', 'click', toggleEventSelectionMode);
safeBindById('copy_selected_messages', 'click', copySelectedMessages);
safeBindById('message_range_selection_mode', 'click', toggleMessageRangeSelectionMode);
safeBindById('clear_message_range_selection', 'click', clearDetailMessageRangeSelection);
safeBindById('detail_message_range_after', 'click', () => {
  applyDetailMessageRange('after');
});
safeBindById('detail_message_range_before', 'click', () => {
  applyDetailMessageRange('before');
});
safeBindById('detail_keyword_q', 'input', () => {
  updateDetailKeywordControls();
});
safeBindById('language_select', 'change', (event) => {
  setUiLanguage(event.target.value);
});
safeBindById('detail_keyword_q', 'keydown', (event) => {
  if(event.key === 'Enter' && !event.isComposing){
    event.preventDefault();
    runDetailKeywordSearch();
    releaseSearchFocus();
  }
});
safeBindById('detail_keyword_filter', 'click', applyDetailKeywordFilter);
safeBindById('detail_keyword_search', 'click', runDetailKeywordSearch);
safeBindById('detail_keyword_prev', 'click', () => {
  moveDetailKeywordSearch(-1);
});
safeBindById('detail_keyword_next', 'click', () => {
  moveDetailKeywordSearch(1);
});
safeBindById('detail_keyword_clear', 'click', clearDetailKeyword);
document.addEventListener('keydown', (event) => {
  if(event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey){
    return;
  }
  if(event.key === 'Escape'){
    if(handleShortcutEscape()){
      event.preventDefault();
    }
    return;
  }
  if(isEditableTarget(event.target)){
    return;
  }
  if(event.key === 'F5'){
    event.preventDefault();
    triggerViewerRefresh();
    return;
  }
  if(event.key === '/'){
    event.preventDefault();
    focusShortcutSearch();
    return;
  }
  if(event.shiftKey){
    if(event.code === 'KeyF'){
      if(triggerButtonShortcut('toggle_filters')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyL'){
      if(triggerButtonShortcut('clear')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyD'){
      if(triggerButtonShortcut('clear_detail')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyT'){
      if(triggerButtonShortcut('toggle_detail_actions')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyR'){
      if(triggerButtonShortcut('copy_resume_command')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyC'){
      if(triggerButtonShortcut('copy_displayed_messages')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyS'){
      if(triggerButtonShortcut('event_selection_mode')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyX'){
      if(triggerButtonShortcut('copy_selected_messages')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyG'){
      if(triggerButtonShortcut('message_range_selection_mode')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyH'){
      if(triggerButtonShortcut('clear_message_range_selection')){
        event.preventDefault();
      }
      return;
    }
    return;
  }
  if(event.code === 'Digit1'){
    if(triggerCheckboxShortcut('only_user_instruction')){
      event.preventDefault();
    }
    return;
  }
  if(event.code === 'Digit2'){
    if(triggerCheckboxShortcut('only_ai_response')){
      event.preventDefault();
    }
    return;
  }
  if(event.code === 'Digit3'){
    if(triggerCheckboxShortcut('turn_boundary_only')){
      event.preventDefault();
    }
    return;
  }
  if(event.code === 'Digit4'){
    if(triggerCheckboxShortcut('reverse_order')){
      event.preventDefault();
    }
    return;
  }
  if(event.code === 'Comma'){
    if(triggerButtonShortcut('detail_message_range_before')){
      event.preventDefault();
    }
    return;
  }
  if(event.code === 'Period'){
    if(triggerButtonShortcut('detail_message_range_after')){
      event.preventDefault();
    }
    return;
  }
  const key = event.key.toLowerCase();
  if(event.code === 'KeyM' || key === 'm'){
    event.preventDefault();
    setDetailMetaVisible(!detailMetaVisible);
    return;
  }
  if(event.key === '[' || event.code === 'BracketLeft'){
    if(openRelativeSession(-1)){
      event.preventDefault();
    }
    return;
  }
  if(event.key === ']' || event.code === 'BracketRight'){
    if(openRelativeSession(1)){
      event.preventDefault();
    }
    return;
  }
  if(key === 'n'){
    if(moveDetailKeywordSearchByShortcut(1)){
      event.preventDefault();
    }
    return;
  }
  if(key === 'p'){
    if(moveDetailKeywordSearchByShortcut(-1)){
      event.preventDefault();
    }
  }
});
document.getElementById('add_session_label').addEventListener('click', async (event) => {
  await addSessionLabelFromButton(event.currentTarget);
});
document.getElementById('events').addEventListener('pointerdown', (event) => {
  if(event.target.closest('pre')){
    detailPointerDown = true;
    noteDetailInteraction();
  }
});
window.addEventListener('pointerup', () => {
  if(!detailPointerDown){
    return;
  }
  detailPointerDown = false;
  noteDetailInteraction();
  scheduleDeferredAutomaticDetailSync();
});
document.addEventListener('selectionchange', () => {
  if(hasDetailTextSelection()){
    noteDetailInteraction();
    return;
  }
  scheduleDeferredAutomaticDetailSync();
});
document.getElementById('open_label_manager').addEventListener('click', openLabelManagerWindow);
document.addEventListener('click', (event) => {
  const picker = document.getElementById('label_picker');
  if(picker.classList.contains('hidden')) return;
  if(picker.contains(event.target)) return;
  if(event.target.closest('.event-label-add-button')) return;
  if(event.target.closest('#add_session_label')) return;
  hideLabelPicker();
});
document.getElementById('shortcut_dialog').addEventListener('click', (event) => {
  if(event.target.id === 'shortcut_dialog'){
    closeShortcutDialog();
  }
});
window.addEventListener('message', async (event) => {
  if(event.origin !== location.origin) return;
  if(labelManagerWindow && !labelManagerWindow.closed && event.source !== labelManagerWindow) return;
  if(!event.data || event.data.type !== 'labels-updated') return;
  await loadLabels(false);
  await loadSessions({ mode: 'labels' });
});
window.addEventListener('focus', async () => {
  await loadLabels(false);
  await loadSessions({ mode: 'focus' });
});
window.addEventListener('storage', (event) => {
  if(event.key !== LANGUAGE_STORAGE_KEY){
    return;
  }
  const nextLanguage = normalizeLanguage(event.newValue || 'ja');
  if(nextLanguage !== uiLanguage){
    setUiLanguage(nextLanguage, false);
  }
});
window.addEventListener('resize', () => {
  updateLeftPaneVisibility();
});
updateCopyResumeButtonState();
updateDisplayedMessagesCopyButtonState();
updateEventSelectionModeButtonState();
updateCopySelectedMessagesButtonState();
updateMessageRangeSelectionModeButtonState();
updateClearMessageRangeSelectionButtonState();
updateMessageRangeFilterButtonsState();
updateDetailKeywordControls({ total: 0 });
updateRefreshDetailButtonState();
updateFilterVisibility();
restoreFilters();
setUiLanguage(getRequestedLanguage(), false);
updateFilterVisibility();
updateDetailMetaVisibility();
updateLeftPaneVisibility();
updateDetailActionsVisibility();
state.isSessionsLoading = true;
renderSessionList();
loadLabels(false)
  .catch(() => {})
  .finally(() => loadSessions({ mode: 'initial' }));
</script>
</body>
</html>
"""


LABELS_PAGE = """<!doctype html>
<html lang=\"ja\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>ラベル管理</title>
<link rel=\"icon\" href=\"/icons/claude-sessions-viewer.svg\" type=\"image/svg+xml\" />
<style>
:root {
  --bg: #f5f8ff;
  --panel: rgba(255, 255, 255, 0.78);
  --panel-strong: rgba(255, 255, 255, 0.94);
  --line: rgba(148, 163, 184, 0.28);
  --line-strong: rgba(148, 163, 184, 0.52);
  --text: #0f172a;
  --muted: #546277;
  --accent: #0f766e;
  --accent-strong: #0b5c57;
  --accent-soft: rgba(15, 118, 110, 0.12);
  --danger: #be123c;
  --shadow: 0 28px 70px rgba(15, 23, 42, 0.14);
  --shadow-soft: 0 16px 36px rgba(15, 23, 42, 0.1);
  --font-sans: "Aptos", "Segoe UI", "Yu Gothic UI", sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  --text-kicker: 10px;
  --text-label: 11px;
  --text-caption: 12px;
  --text-body: 13px;
  --text-title-sm: 16px;
  --text-title-md: 18px;
  --text-title-lg: 20px;
  --text-display: clamp(28px, 1.55vw, 32px);
  --text-display-compact: 28px;
  --space-1: 4px;
  --space-2: 6px;
  --space-3: 8px;
  --space-4: 10px;
  --space-5: 12px;
  --space-6: 16px;
  --space-7: 18px;
  --space-8: 24px;
}
* { box-sizing: border-box; }
html, body { min-height: 100%; }
body {
  margin: 0;
  position: relative;
  overflow-x: hidden;
  font-family: var(--font-sans);
  font-size: var(--text-body);
  line-height: 1.5;
  background:
    radial-gradient(circle at 12% 18%, rgba(59, 130, 246, 0.18), transparent 24%),
    radial-gradient(circle at 88% 14%, rgba(15, 118, 110, 0.16), transparent 22%),
    linear-gradient(180deg, #eef6ff 0%, #f8fbff 54%, #eef4fb 100%);
  color: var(--text);
}
body::before,
body::after {
  content: "";
  position: fixed;
  width: 320px;
  height: 320px;
  border-radius: 999px;
  filter: blur(36px);
  pointer-events: none;
  opacity: 0.55;
}
body::before {
  top: -120px;
  left: -90px;
  background: rgba(96, 165, 250, 0.22);
}
body::after {
  right: -120px;
  bottom: -140px;
  background: rgba(16, 185, 129, 0.18);
}
.page {
  position: relative;
  z-index: 1;
  max-width: 980px;
  margin: 0 auto;
  padding: 30px 18px 40px;
}
.page-header {
  margin-bottom: var(--space-6);
}
.page-header-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-5);
}
.page-actions {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}
.eyebrow {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-4);
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.78);
  background: rgba(255, 255, 255, 0.72);
  color: #0f5a73;
  font-size: var(--text-kicker);
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.08);
}
.hero-title {
  margin: var(--space-4) 0 0;
  font-size: var(--text-display);
  line-height: 1.08;
  letter-spacing: -0.03em;
}
.hero-copy {
  margin-top: var(--space-3);
  max-width: 760px;
  color: var(--muted);
  font-size: var(--text-body);
  line-height: 1.6;
}
.panel {
  position: relative;
  overflow: hidden;
  background: var(--panel);
  border: 1px solid rgba(255, 255, 255, 0.7);
  border-radius: 22px;
  padding: var(--space-6);
  box-shadow: 0 22px 48px rgba(15, 23, 42, 0.12);
  backdrop-filter: blur(18px);
}
.panel::before {
  content: "";
  position: absolute;
  inset: 0 0 auto 0;
  height: 80px;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.42), transparent);
  pointer-events: none;
}
.panel + .panel {
  margin-top: var(--space-6);
}
.editor-panel {
  padding: var(--space-6);
}
.list-panel {
  padding: var(--space-6);
}
.panel-head,
.list-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-5);
  margin-bottom: var(--space-5);
}
.editor-panel .panel-head {
  align-items: flex-start;
  margin-bottom: var(--space-4);
}
.editor-panel .panel-title {
  margin-top: 2px;
  font-size: var(--text-title-md);
}
.editor-panel .panel-copy {
  margin-top: 2px;
  max-width: 520px;
  font-size: var(--text-caption);
  line-height: 1.5;
}
.editor-panel .panel-chip {
  align-self: flex-start;
  margin-top: 0;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-kicker);
}
.list-head {
  align-items: center;
  margin-bottom: var(--space-3);
}
.list-head > div:first-child {
  min-width: 0;
}
.list-head .panel-title {
  margin-top: 2px;
  font-size: var(--text-title-md);
}
.list-head .panel-chip {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-kicker);
  align-self: center;
}
.panel-kicker {
  color: #0f5a73;
  font-size: var(--text-kicker);
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.panel-title {
  margin-top: var(--space-2);
  font-size: var(--text-title-lg);
  line-height: 1.15;
  letter-spacing: -0.02em;
}
.panel-copy,
.muted {
  color: var(--muted);
  font-size: var(--text-body);
  line-height: 1.6;
}
.panel-chip {
  flex: 0 0 auto;
  align-self: center;
  padding: var(--space-2) var(--space-4);
  border-radius: 999px;
  border: 1px solid rgba(15, 118, 110, 0.12);
  background: rgba(15, 118, 110, 0.08);
  color: var(--accent-strong);
  font-size: var(--text-label);
  font-weight: 700;
}
.form-grid {
  display: grid;
  grid-template-columns: 1.4fr 1fr 1.1fr auto;
  gap: var(--space-4);
  align-items: end;
}
.editor-panel .form-grid {
  gap: var(--space-3);
}
label {
  display: grid;
  gap: var(--space-1);
  font-size: var(--text-kicker);
  color: #475569;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
input, select, button {
  font-family: inherit;
  font-size: var(--text-body);
  line-height: 1.4;
}
input, select {
  min-height: 40px;
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  padding: var(--space-4) var(--space-5);
  background: rgba(255, 255, 255, 0.86);
  color: var(--text);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
  transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
}
input::placeholder {
  color: #94a3b8;
}
input:focus,
select:focus {
  outline: none;
  border-color: rgba(15, 118, 110, 0.5);
  box-shadow: 0 0 0 4px rgba(15, 118, 110, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.8);
}
.language-select {
  min-width: 132px;
  padding-right: 34px;
  background-image:
    linear-gradient(45deg, transparent 50%, #0f766e 50%),
    linear-gradient(135deg, #0f766e 50%, transparent 50%);
  background-position:
    calc(100% - 18px) calc(50% - 2px),
    calc(100% - 12px) calc(50% - 2px);
  background-size: 6px 6px, 6px 6px;
  background-repeat: no-repeat;
  appearance: none;
}
button {
  min-height: 36px;
  border: 0;
  border-radius: 12px;
  padding: 0 var(--space-6);
  background: linear-gradient(135deg, var(--accent) 0%, #16938a 100%);
  color: #ffffff;
  cursor: pointer;
  font-weight: 700;
  letter-spacing: 0.01em;
  box-shadow: 0 8px 16px rgba(15, 118, 110, 0.14);
  transition: transform 0.18s ease, box-shadow 0.18s ease, opacity 0.18s ease;
}
button:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 20px rgba(15, 118, 110, 0.16);
}
button:active {
  transform: translateY(0);
  box-shadow: 0 4px 10px rgba(15, 118, 110, 0.12);
}
.secondary {
  background: linear-gradient(135deg, #64748b 0%, #475569 100%);
  box-shadow: 0 8px 18px rgba(71, 85, 105, 0.14);
}
.danger {
  background: linear-gradient(135deg, var(--danger) 0%, #e11d48 100%);
  box-shadow: 0 8px 18px rgba(190, 18, 60, 0.14);
}
.preset-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.preset-field {
  display: grid;
  gap: 6px;
  align-self: stretch;
}
.preset-field-title {
  font-size: var(--text-kicker);
  color: #475569;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.badge {
  --label-color: #94a3b8;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid rgba(148, 163, 184, 0.3);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.9);
  padding: 5px 8px;
  font-size: var(--text-kicker);
  font-weight: 700;
  line-height: 1;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.78);
}
.preset-list.inline {
  margin-top: 0;
}
.preset-badge {
  min-height: 24px;
  color: #334155;
  background: rgba(255, 255, 255, 0.72);
  border-color: rgba(148, 163, 184, 0.24);
  border-radius: 8px;
  padding: 0 7px;
  font-weight: 600;
  box-shadow: none;
}
.preset-badge.active {
  border-color: var(--label-color);
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.1), 0 10px 18px rgba(15, 23, 42, 0.06);
}
.preset-badge .dot {
  width: 7px;
  height: 7px;
  box-shadow: none;
}
.badge .dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--label-color);
  box-shadow: 0 0 0 3px rgba(148, 163, 184, 0.14);
}
.label-list {
  display: grid;
  gap: 6px;
  margin-top: 8px;
  padding: 0;
}
.label-row {
  border: 1px solid rgba(226, 232, 240, 0.92);
  border-radius: 14px;
  padding: 10px 12px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.96), rgba(247, 250, 255, 0.92));
  box-shadow: var(--shadow-soft);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.label-row:hover {
  transform: translateY(-1px);
  box-shadow: 0 14px 26px rgba(15, 23, 42, 0.1);
}
.label-main {
  display: block;
  min-width: 0;
}
.label-topline {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.label-badge {
  width: fit-content;
  max-width: 100%;
  color: #1e293b;
  background: #ffffff;
  border-color: var(--label-color);
  padding: 5px 8px 5px 8px;
  font-size: var(--text-caption);
}
.label-badge .dot {
  width: 8px;
  height: 8px;
  flex: 0 0 auto;
  box-shadow: none;
  opacity: 1;
  filter: none;
}
.label-meta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 2px;
  font-size: var(--text-caption);
  color: var(--muted);
}
.label-meta-prefix {
  color: #64748b;
  font-size: var(--text-label);
}
.label-code {
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  margin-left: 0;
  border-radius: 999px;
  border: 1px solid rgba(148, 163, 184, 0.24);
  background: rgba(238, 246, 255, 0.9);
  color: #0f3d57;
  font-family: var(--font-mono);
  font-size: var(--text-caption);
}
.label-row-actions {
  display: flex;
  gap: 6px;
  flex-wrap: nowrap;
  align-items: center;
  justify-content: flex-end;
}
.label-row-actions button {
  min-height: 30px;
  border-radius: 10px;
  padding: 0 10px;
  font-size: var(--text-caption);
  box-shadow: none;
}
.label-row-actions button:hover {
  box-shadow: 0 6px 12px rgba(15, 23, 42, 0.08);
}
.dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(15, 23, 42, 0.48);
  backdrop-filter: blur(12px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 14px;
}
.dialog-backdrop.hidden {
  display: none;
}
.dialog {
  position: relative;
  overflow: hidden;
  z-index: 1;
  width: min(380px, 100%);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.97), rgba(248, 251, 255, 0.94));
  border: 1px solid rgba(255, 255, 255, 0.78);
  border-radius: 20px;
  box-shadow: 0 24px 56px rgba(15, 23, 42, 0.24);
  padding: 18px;
}
.dialog::before {
  content: "";
  position: absolute;
  inset: 0 0 auto 0;
  height: 6px;
  background: linear-gradient(90deg, #fb7185 0%, #f59e0b 52%, #22c55e 100%);
}
.dialog-kicker {
  color: #be123c;
  font-size: var(--text-label);
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.dialog-title {
  margin: 8px 0 0;
  font-size: var(--text-title-lg);
  letter-spacing: -0.02em;
}
.dialog-message {
  margin-top: 10px;
  color: #334155;
  font-size: var(--text-body);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.dialog-actions {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
.empty-state {
  border: 1px dashed rgba(148, 163, 184, 0.4);
  border-radius: 18px;
  padding: 20px;
  text-align: center;
  background: rgba(255, 255, 255, 0.56);
  color: var(--muted);
  font-size: var(--text-body);
}
@media (max-width: 760px) {
  .page {
    padding: 24px 14px 32px;
  }
  .hero-title {
    font-size: var(--text-display-compact);
  }
  .panel {
    padding: 16px;
  }
  .form-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 560px) {
  .page-header-top {
    flex-direction: column;
    align-items: flex-start;
  }
  .page-actions {
    width: 100%;
  }
  .language-select {
    width: 100%;
  }
  .panel-head {
    flex-direction: column;
    align-items: flex-start;
  }
  .label-row {
    grid-template-columns: 1fr;
    align-items: start;
  }
  .label-row-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
</head>
<body>
<div class="page">
  <div class="page-header">
    <div class="page-header-top">
      <div class="eyebrow" id="page_badge">Claude Sessions Viewer</div>
      <div class="page-actions">
        <select id="language_select" class="language-select" aria-label="Language">
          <option value="ja">日本語</option>
          <option value="en">English</option>
          <option value="zh-Hans">简体中文</option>
          <option value="zh-Hant">繁體中文</option>
        </select>
      </div>
    </div>
    <h1 class="hero-title" id="hero_title">ラベル管理</h1>
    <div class="hero-copy" id="hero_copy">セッションとイベントに共通で使うラベルをここで整えます。色コードを直接入力するか、プリセットをクリックして素早く設定できます。</div>
  </div>
  <div class="panel editor-panel">
    <div class="panel-head">
      <div>
        <div class="panel-kicker" id="editor_kicker">Label Editor</div>
        <div class="panel-title" id="editor_title">新規作成 / 編集</div>
        <div class="panel-copy" id="editor_copy">保存すると一覧フィルタと詳細画面の両方にすぐ反映されます。</div>
      </div>
      <div class="panel-chip" id="editor_chip">即時反映</div>
    </div>
    <div class="form-grid">
      <label>
        <span id="label_name_text">ラベル名</span>
        <input id="label_name" placeholder="例: README / 画像 / 再確認" />
      </label>
      <label>
        <span id="label_color_text">色コード</span>
        <input id="label_color" placeholder="#3b82f6 / rgb(...) / oklch(...)" />
      </label>
      <div class="preset-field">
        <div class="preset-field-title" id="preset_field_title">色プリセット</div>
        <div class="preset-list inline" id="preset_preview"></div>
      </div>
      <button id="save_label">保存</button>
    </div>
    <input id="label_id" type="hidden" />
    <input id="label_family" type="hidden" />
  </div>

  <div class="panel list-panel">
    <div class="list-head">
      <div>
        <div class="panel-kicker" id="list_kicker">Registered Labels</div>
        <div class="panel-title" id="list_title">既存ラベル</div>
      </div>
      <div class="panel-chip" id="label_count_badge">0 labels</div>
    </div>
    <div class="label-list" id="label_list"></div>
  </div>
</div>
<div id="error_dialog" class="dialog-backdrop hidden">
  <div class="dialog" role="alertdialog" aria-modal="true" aria-labelledby="error_dialog_title">
    <div class="dialog-kicker" id="error_dialog_kicker">入力チェック</div>
    <h2 class="dialog-title" id="error_dialog_title">入力エラー</h2>
    <div class="dialog-message" id="error_dialog_message"></div>
    <div class="dialog-actions">
      <button id="error_dialog_close" type="button">閉じる</button>
    </div>
  </div>
</div>
<script>
const LANGUAGE_STORAGE_KEY = 'claude_sessions_viewer_language_v1';
const SUPPORTED_LANGUAGES = ['ja', 'en', 'zh-Hans', 'zh-Hant'];
const LABEL_I18N = {
  ja: {
    'language.selector': '言語',
    'page.title': 'ラベル管理 | Claude Sessions Viewer',
    'page.heroTitle': 'ラベル管理',
    'page.heroCopy': 'セッションとイベントに共通で使うラベルをここで整えます。色コードを直接入力するか、プリセットをクリックして素早く設定できます。',
    'editor.kicker': 'Label Editor',
    'editor.title': '新規作成 / 編集',
    'editor.copy': '保存すると一覧フィルタと詳細画面の両方にすぐ反映されます。',
    'editor.chip': '即時反映',
    'form.name': 'ラベル名',
    'form.color': '色コード',
    'form.presets': '色プリセット',
    'form.save': '保存',
    'form.name.placeholder': '例: README / 画像 / 再確認',
    'form.color.placeholder': '#3b82f6 / rgb(...) / oklch(...)',
    'list.kicker': 'Registered Labels',
    'list.title': '既存ラベル',
    'list.count': '{count}件',
    'list.empty': 'ラベルはまだありません。上のフォームから最初のラベルを作成してください。',
    'list.colorPrefix': 'color',
    'action.edit': '編集',
    'action.delete': '削除',
    'dialog.validation.kicker': '入力チェック',
    'dialog.validation.title': '入力エラー',
    'dialog.error.kicker': 'エラーメッセージ',
    'dialog.error.title': 'エラー',
    'dialog.close': '閉じる',
    'confirm.delete': 'このラベルを削除しますか？',
    'preset.red': '赤系',
    'preset.blue': '青系',
    'preset.green': '緑系',
    'preset.yellow': '黄色系',
    'preset.purple': '紫系',
    'error.loadFailed': 'ラベル一覧の取得に失敗しました。',
    'error.saveFailed': 'ラベルの保存に失敗しました。',
    'error.deleteFailed': 'ラベルの削除に失敗しました。',
    'server.colorInvalid': '色コードの形式が不正です',
    'server.colorRequired': '色コードを入力してください',
    'server.nameRequired': 'ラベル名を入力してください',
    'server.nameTooLong': 'ラベル名が長すぎます',
    'server.labelMissing': 'ラベルが見つかりません',
    'server.labelDuplicate': '同名のラベルは既に存在します',
    'server.notFound': '見つかりません',
    'server.labelIdRequired': 'ラベルIDが必要です',
  },
  en: {
    'language.selector': 'Language',
    'page.title': 'Label Manager | Claude Sessions Viewer',
    'page.heroTitle': 'Label Manager',
    'page.heroCopy': 'Manage the shared labels used across sessions and events. Enter a color directly or click a preset for quick setup.',
    'editor.kicker': 'Label Editor',
    'editor.title': 'Create / Edit',
    'editor.copy': 'Saving updates both the list filters and the detail view immediately.',
    'editor.chip': 'Live update',
    'form.name': 'Label name',
    'form.color': 'Color value',
    'form.presets': 'Color presets',
    'form.save': 'Save',
    'form.name.placeholder': 'Example: README / Images / Recheck',
    'form.color.placeholder': '#3b82f6 / rgb(...) / oklch(...)',
    'list.kicker': 'Registered Labels',
    'list.title': 'Existing labels',
    'list.count': '{count} labels',
    'list.empty': 'No labels yet. Create your first label from the form above.',
    'list.colorPrefix': 'color',
    'action.edit': 'Edit',
    'action.delete': 'Delete',
    'dialog.validation.kicker': 'Validation',
    'dialog.validation.title': 'Input error',
    'dialog.error.kicker': 'Error message',
    'dialog.error.title': 'Error',
    'dialog.close': 'Close',
    'confirm.delete': 'Delete this label?',
    'preset.red': 'Red',
    'preset.blue': 'Blue',
    'preset.green': 'Green',
    'preset.yellow': 'Yellow',
    'preset.purple': 'Purple',
    'error.loadFailed': 'Failed to load labels.',
    'error.saveFailed': 'Failed to save the label.',
    'error.deleteFailed': 'Failed to delete the label.',
    'server.colorInvalid': 'The color value format is invalid.',
    'server.colorRequired': 'Enter a color value.',
    'server.nameRequired': 'Enter a label name.',
    'server.nameTooLong': 'The label name is too long.',
    'server.labelMissing': 'The label was not found.',
    'server.labelDuplicate': 'A label with the same name already exists.',
    'server.notFound': 'Not found.',
    'server.labelIdRequired': 'A label ID is required.',
  },
  'zh-Hans': {
    'language.selector': '语言',
    'page.title': '标签管理 | Claude Sessions Viewer',
    'page.heroTitle': '标签管理',
    'page.heroCopy': '在这里整理会话与事件共用的标签。可以直接输入颜色值，也可以点击预设快速设置。',
    'editor.kicker': 'Label Editor',
    'editor.title': '新建 / 编辑',
    'editor.copy': '保存后会立即反映到列表筛选和详情视图。',
    'editor.chip': '即时生效',
    'form.name': '标签名',
    'form.color': '颜色值',
    'form.presets': '颜色预设',
    'form.save': '保存',
    'form.name.placeholder': '例如: README / 图片 / 再确认',
    'form.color.placeholder': '#3b82f6 / rgb(...) / oklch(...)',
    'list.kicker': 'Registered Labels',
    'list.title': '现有标签',
    'list.count': '{count} 个标签',
    'list.empty': '还没有标签。请先在上面的表单中创建第一个标签。',
    'list.colorPrefix': 'color',
    'action.edit': '编辑',
    'action.delete': '删除',
    'dialog.validation.kicker': '输入检查',
    'dialog.validation.title': '输入错误',
    'dialog.error.kicker': '错误信息',
    'dialog.error.title': '错误',
    'dialog.close': '关闭',
    'confirm.delete': '要删除这个标签吗？',
    'preset.red': '红色系',
    'preset.blue': '蓝色系',
    'preset.green': '绿色系',
    'preset.yellow': '黄色系',
    'preset.purple': '紫色系',
    'error.loadFailed': '获取标签列表失败。',
    'error.saveFailed': '保存标签失败。',
    'error.deleteFailed': '删除标签失败。',
    'server.colorInvalid': '颜色值格式无效。',
    'server.colorRequired': '请输入颜色值。',
    'server.nameRequired': '请输入标签名。',
    'server.nameTooLong': '标签名过长。',
    'server.labelMissing': '未找到标签。',
    'server.labelDuplicate': '已存在同名标签。',
    'server.notFound': '未找到。',
    'server.labelIdRequired': '需要标签 ID。',
  },
};
LABEL_I18N['zh-Hant'] = {
  ...LABEL_I18N['zh-Hans'],
  'language.selector': '語言',
  'page.title': '標籤管理 | Claude Sessions Viewer',
  'page.heroTitle': '標籤管理',
  'page.heroCopy': '在這裡整理工作階段與事件共用的標籤。可以直接輸入顏色值，也可以點擊預設快速設定。',
  'editor.title': '新增 / 編輯',
  'editor.copy': '儲存後會立即反映到列表篩選與詳情視圖。',
  'editor.chip': '即時生效',
  'form.name': '標籤名',
  'form.color': '顏色值',
  'form.presets': '顏色預設',
  'form.save': '儲存',
  'form.name.placeholder': '例如: README / 圖片 / 再確認',
  'list.title': '現有標籤',
  'list.count': '{count} 個標籤',
  'list.empty': '還沒有標籤。請先在上面的表單中建立第一個標籤。',
  'action.edit': '編輯',
  'action.delete': '刪除',
  'dialog.validation.kicker': '輸入檢查',
  'dialog.validation.title': '輸入錯誤',
  'dialog.error.kicker': '錯誤訊息',
  'dialog.error.title': '錯誤',
  'dialog.close': '關閉',
  'confirm.delete': '要刪除這個標籤嗎？',
  'preset.red': '紅色系',
  'preset.blue': '藍色系',
  'preset.green': '綠色系',
  'preset.yellow': '黃色系',
  'preset.purple': '紫色系',
  'error.loadFailed': '取得標籤列表失敗。',
  'error.saveFailed': '儲存標籤失敗。',
  'error.deleteFailed': '刪除標籤失敗。',
  'server.colorInvalid': '顏色值格式無效。',
  'server.colorRequired': '請輸入顏色值。',
  'server.nameRequired': '請輸入標籤名。',
  'server.nameTooLong': '標籤名過長。',
  'server.labelMissing': '找不到標籤。',
  'server.labelDuplicate': '已存在同名標籤。',
  'server.notFound': '找不到項目。',
  'server.labelIdRequired': '需要標籤 ID。',
};
const PRESETS = {
  red: { color: '#ef4444' },
  blue: { color: '#3b82f6' },
  green: { color: '#22c55e' },
  yellow: { color: '#eab308' },
  purple: { color: '#a855f7' },
};
const SERVER_ERROR_KEYS = {
  '色コードの形式が不正です': 'server.colorInvalid',
  '色コードを入力してください': 'server.colorRequired',
  'ラベル名を入力してください': 'server.nameRequired',
  'ラベル名が長すぎます': 'server.nameTooLong',
  'ラベルが見つかりません': 'server.labelMissing',
  '同名のラベルは既に存在します': 'server.labelDuplicate',
  'not found': 'server.notFound',
  'label id is required': 'server.labelIdRequired',
};

let uiLanguage = 'ja';
let labelItems = [];
let errorDialogTone = 'validation';
let errorDialogMessage = '';

function normalizeLanguage(value){
  const raw = (value || '').trim();
  if(raw === 'zh' || raw === 'zh-CN' || raw === 'zh-SG'){
    return 'zh-Hans';
  }
  if(raw === 'zh-TW' || raw === 'zh-HK' || raw === 'zh-MO'){
    return 'zh-Hant';
  }
  return SUPPORTED_LANGUAGES.includes(raw) ? raw : 'ja';
}

function getStoredLanguage(){
  try {
    return localStorage.getItem(LANGUAGE_STORAGE_KEY) || '';
  } catch (e) {
    return '';
  }
}

function getRequestedLanguage(){
  const params = new URLSearchParams(window.location.search);
  return normalizeLanguage(params.get('lang') || getStoredLanguage() || uiLanguage);
}

function t(key, vars){
  const dict = LABEL_I18N[uiLanguage] || LABEL_I18N.ja;
  let text = dict[key];
  if(typeof text !== 'string'){
    text = LABEL_I18N.ja[key] || key;
  }
  if(vars){
    Object.entries(vars).forEach(([name, value]) => {
      text = text.replaceAll(`{${name}}`, String(value));
    });
  }
  return text;
}

function esc(s){
  return (s ?? '').toString().replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
}

function presetLabel(family){
  if(!family) return '';
  return t(`preset.${family}`);
}

function translateServerError(message){
  const key = SERVER_ERROR_KEYS[message || ''];
  return key ? t(key) : (message || '');
}

function badgeHtml(label){
  return `<span class="badge label-badge" style="--label-color:${esc(label.color_value)}"><span class="dot"></span><span>${esc(label.name)}</span></span>`;
}

function renderLabelCount(count){
  if(uiLanguage === 'en' && count === 1){
    return '1 label';
  }
  return t('list.count', { count });
}

function applyDialogLanguage(){
  const validation = errorDialogTone !== 'error';
  document.getElementById('error_dialog_kicker').textContent = validation ? t('dialog.validation.kicker') : t('dialog.error.kicker');
  document.getElementById('error_dialog_title').textContent = validation ? t('dialog.validation.title') : t('dialog.error.title');
  document.getElementById('error_dialog_message').textContent = translateServerError(errorDialogMessage);
  document.getElementById('error_dialog_close').textContent = t('dialog.close');
}

function applyLabelLanguage(){
  document.documentElement.lang = uiLanguage;
  document.title = t('page.title');
  document.getElementById('language_select').value = uiLanguage;
  document.getElementById('language_select').setAttribute('aria-label', t('language.selector'));
  document.getElementById('hero_title').textContent = t('page.heroTitle');
  document.getElementById('hero_copy').textContent = t('page.heroCopy');
  document.getElementById('editor_kicker').textContent = t('editor.kicker');
  document.getElementById('editor_title').textContent = t('editor.title');
  document.getElementById('editor_copy').textContent = t('editor.copy');
  document.getElementById('editor_chip').textContent = t('editor.chip');
  document.getElementById('label_name_text').textContent = t('form.name');
  document.getElementById('label_color_text').textContent = t('form.color');
  document.getElementById('preset_field_title').textContent = t('form.presets');
  document.getElementById('save_label').textContent = t('form.save');
  document.getElementById('label_name').placeholder = t('form.name.placeholder');
  document.getElementById('label_color').placeholder = t('form.color.placeholder');
  document.getElementById('list_kicker').textContent = t('list.kicker');
  document.getElementById('list_title').textContent = t('list.title');
  applyDialogLanguage();
  renderPresetPreview();
  renderLabelList();
}

function setUiLanguage(nextLanguage, persist){
  uiLanguage = normalizeLanguage(nextLanguage);
  if(persist !== false){
    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, uiLanguage);
    } catch (e) {
      // Ignore storage write errors.
    }
  }
  applyLabelLanguage();
}

function showErrorDialog(message, tone){
  errorDialogTone = tone === 'error' ? 'error' : 'validation';
  errorDialogMessage = message || '';
  applyDialogLanguage();
  document.getElementById('error_dialog').classList.remove('hidden');
}

function hideErrorDialog(){
  document.getElementById('error_dialog').classList.add('hidden');
}

function notifyParent(){
  if(window.opener && !window.opener.closed){
    window.opener.postMessage({ type: 'labels-updated' }, location.origin);
  }
}

async function postJson(url, payload){
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  return r.json();
}

function renderPresetPreview(){
  const box = document.getElementById('preset_preview');
  const selectedFamily = document.getElementById('label_family').value || '';
  box.innerHTML = Object.entries(PRESETS).map(([key, value]) =>
    `<button type="button" class="badge preset-badge ${selectedFamily === key ? 'active' : ''}" data-family="${esc(key)}" data-color="${esc(value.color)}" style="--label-color:${esc(value.color)}"><span class="dot"></span><span>${esc(presetLabel(key))}</span></button>`
  ).join('');
  box.querySelectorAll('.preset-badge').forEach(button => {
    button.onclick = () => {
      document.getElementById('label_color').value = button.dataset.color || '';
      document.getElementById('label_family').value = button.dataset.family || '';
      renderPresetPreview();
    };
  });
}

function resetForm(){
  document.getElementById('label_id').value = '';
  document.getElementById('label_name').value = '';
  document.getElementById('label_color').value = '';
  document.getElementById('label_family').value = '';
  renderPresetPreview();
}

function editLabel(label){
  document.getElementById('label_id').value = label.id;
  document.getElementById('label_name').value = label.name;
  document.getElementById('label_color').value = label.color_value;
  document.getElementById('label_family').value = label.color_family || '';
  renderPresetPreview();
}

function renderLabelList(){
  const list = document.getElementById('label_list');
  const countBadge = document.getElementById('label_count_badge');
  countBadge.textContent = renderLabelCount(labelItems.length);
  if(!labelItems.length){
    list.innerHTML = `<div class="empty-state">${esc(t('list.empty'))}</div>`;
    return;
  }
  list.innerHTML = labelItems.map(label => {
    const familyText = label.color_family ? ` / ${esc(presetLabel(label.color_family))}` : '';
    return `
      <div class="label-row">
        <div class="label-main">
          <div class="label-topline">
            ${badgeHtml(label)}
            <div class="label-meta"><span class="label-meta-prefix">${esc(t('list.colorPrefix'))}</span><span class="label-code">${esc(label.color_value)}</span>${familyText}</div>
          </div>
        </div>
        <div class="label-row-actions">
          <button class="secondary edit-label" data-label-id="${esc(label.id)}">${esc(t('action.edit'))}</button>
          <button class="danger delete-label" data-label-id="${esc(label.id)}">${esc(t('action.delete'))}</button>
        </div>
      </div>
    `;
  }).join('');
  list.querySelectorAll('.edit-label').forEach(button => {
    button.onclick = () => {
      const label = labelItems.find(item => String(item.id) === button.dataset.labelId);
      if(label) editLabel(label);
    };
  });
  list.querySelectorAll('.delete-label').forEach(button => {
    button.onclick = async () => {
      await deleteLabel(Number(button.dataset.labelId));
    };
  });
}

async function deleteLabel(id){
  if(!confirm(t('confirm.delete'))) return;
  try {
    const data = await postJson('/api/labels/delete', { id });
    if(data.error){
      showErrorDialog(data.error, 'error');
      return;
    }
    notifyParent();
    await loadLabels();
    resetForm();
  } catch (error) {
    showErrorDialog(t('error.deleteFailed'), 'error');
  }
}

async function loadLabels(){
  try {
    const r = await fetch('/api/labels?ts=' + Date.now(), { cache: 'no-store' });
    const data = await r.json();
    labelItems = data.labels || [];
    renderLabelList();
  } catch (error) {
    showErrorDialog(t('error.loadFailed'), 'error');
  }
}

document.getElementById('save_label').addEventListener('click', async () => {
  const payload = {
    id: document.getElementById('label_id').value || null,
    name: document.getElementById('label_name').value,
    color_value: document.getElementById('label_color').value,
    color_family: document.getElementById('label_family').value,
  };
  try {
    const data = await postJson('/api/labels/save', payload);
    if(data.error){
      showErrorDialog(data.error, 'validation');
      return;
    }
    notifyParent();
    await loadLabels();
    resetForm();
  } catch (error) {
    showErrorDialog(t('error.saveFailed'), 'error');
  }
});

document.getElementById('language_select').addEventListener('change', (event) => {
  setUiLanguage(event.target.value);
});
document.getElementById('error_dialog_close').addEventListener('click', hideErrorDialog);
document.getElementById('error_dialog').addEventListener('click', (event) => {
  if(event.target.id === 'error_dialog'){
    hideErrorDialog();
  }
});
document.addEventListener('keydown', (event) => {
  if(event.key === 'Escape'){
    hideErrorDialog();
  }
});
document.getElementById('label_color').addEventListener('input', () => {
  const color = document.getElementById('label_color').value.trim().toLowerCase();
  const matched = Object.entries(PRESETS).find(([, value]) => value.color.toLowerCase() === color);
  document.getElementById('label_family').value = matched ? matched[0] : '';
  renderPresetPreview();
});
window.addEventListener('storage', (event) => {
  if(event.key !== LANGUAGE_STORAGE_KEY){
    return;
  }
  const nextLanguage = normalizeLanguage(event.newValue || 'ja');
  if(nextLanguage !== uiLanguage){
    setUiLanguage(nextLanguage, false);
  }
});

setUiLanguage(getRequestedLanguage(), false);
loadLabels();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send_raw(self, raw: bytes, content_type: str, status=200):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def _send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send_raw(raw, "application/json; charset=utf-8", status)

    def _send_html(self, text, status=200):
        raw = text.encode("utf-8")
        self._send_raw(raw, "text/html; charset=utf-8", status)

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/icons/'):
            icon_name = Path(parsed.path).name
            icon_path = ICON_DIR / icon_name
            if icon_path.suffix == '.svg' and icon_path.is_file():
                self._send_raw(icon_path.read_bytes(), 'image/svg+xml; charset=utf-8')
                return
            self._send_html('<h1>404</h1>', 404)
            return

        if parsed.path == "/":
            self._send_html(HTML_PAGE)
            return
        if parsed.path == "/labels":
            self._send_html(LABELS_PAGE)
            return
        if parsed.path == "/api/labels":
            self._send_json({"labels": list_labels()})
            return
        if parsed.path == "/api/sessions":
            roots = get_roots()
            items = iter_all_session_files()[:MAX_LIST]
            q = urllib.parse.parse_qs(parsed.query)
            raw_query = (q.get("q", [""])[0] or "").strip()
            mode = q.get("mode", ["and"])[0].strip().lower()
            if mode not in ("and", "or"):
                mode = "and"
            sort = q.get("sort", ["desc"])[0].strip().lower()
            if sort not in ("desc", "asc", "updated"):
                sort = "desc"
            session_label_id = parse_optional_int(q.get("session_label_id", [""])[0])
            event_label_id = parse_optional_int(q.get("event_label_id", [""])[0])
            sync_search_index(items, prune_missing=True)
            sessions = fetch_sessions_from_search_index(
                raw_query,
                mode,
                MAX_LIST,
                session_label_id=session_label_id,
                event_label_id=event_label_id,
                sort=sort,
            )
            root_parts = []
            if roots["claude_cli"]:
                root_parts.extend(f"Claude Code CLI: {x}" for x in roots["claude_cli"])
            if roots["claude_desktop"]:
                root_parts.extend(f"Claude Desktop: {x}" for x in roots["claude_desktop"])
            self._send_json(
                {
                    "root": " | ".join(root_parts),
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
            try:
                source_type, p, chosen_root = resolve_session_item(raw_path, source_type)
            except ValueError as exc:
                status = 404 if str(exc) == "session file not found" else 400
                self._send_json({"error": str(exc)}, status)
                return
            sync_search_index([(source_type, p, chosen_root)], prune_missing=False)
            stat_result, signature = get_session_signature(p)
            session = fetch_session_summary_from_index(session_path_key(p)) or summarize_session(
                source_type,
                p,
                chosen_root,
                stat_result=stat_result,
                signature=signature,
            )
            data = load_session_events(source_type, p, stat_result=stat_result, signature=signature)
            data["session"] = session
            self._send_json(data)
            return
        self._send_html("<h1>404</h1>", 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            body = parse_json_body(self)
            if parsed.path == "/api/labels/save":
                label_id = parse_optional_int(body.get("id"))
                label = save_label(label_id, body.get("name", ""), body.get("color_value", ""), body.get("color_family", ""))
                self._send_json({"label": label})
                return
            if parsed.path == "/api/labels/delete":
                label_id = parse_optional_int(body.get("id"))
                if label_id is None:
                    self._send_json({"error": "label id is required"}, 400)
                    return
                delete_label(label_id)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/session-label/add":
                raw_path = (body.get("path", "") or "").strip()
                label_id = parse_optional_int(body.get("label_id"))
                if not raw_path or label_id is None:
                    self._send_json({"error": "path and label id are required"}, 400)
                    return
                _, path, _ = resolve_session_item(raw_path)
                assign_session_label(path, label_id)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/session-label/remove":
                raw_path = (body.get("path", "") or "").strip()
                label_id = parse_optional_int(body.get("label_id"))
                if not raw_path or label_id is None:
                    self._send_json({"error": "path and label id are required"}, 400)
                    return
                _, path, _ = resolve_session_item(raw_path)
                remove_session_label(path, label_id)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/event-label/add":
                raw_path = (body.get("path", "") or "").strip()
                event_id = (body.get("event_id", "") or "").strip()
                label_id = parse_optional_int(body.get("label_id"))
                if not raw_path or not event_id or label_id is None:
                    self._send_json({"error": "path, event id and label id are required"}, 400)
                    return
                _, path, _ = resolve_session_item(raw_path)
                assign_event_label(path, event_id, label_id)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/event-label/remove":
                raw_path = (body.get("path", "") or "").strip()
                event_id = (body.get("event_id", "") or "").strip()
                label_id = parse_optional_int(body.get("label_id"))
                if not raw_path or not event_id or label_id is None:
                    self._send_json({"error": "path, event id and label id are required"}, 400)
                    return
                _, path, _ = resolve_session_item(raw_path)
                remove_event_label(path, event_id, label_id)
                self._send_json({"ok": True})
                return
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        self._send_json({"error": "not found"}, 404)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Viewer: http://{HOST}:{PORT}")
    print("Claude Code CLI roots:")
    for path in get_claude_cli_roots():
        print(f"  - {path}")
    print("Claude Desktop roots:")
    for path in get_claude_desktop_roots():
        print(f"  - {path}")
    server.serve_forever()


if __name__ == "__main__":
    main()
