#!/usr/bin/env python3
import json
import os
import re
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8767"))
MAX_LIST = 400
MAX_EVENTS = 4000
MAX_DESKTOP_SCAN_BYTES = 2 * 1024 * 1024


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

    candidates = _unique_paths(candidates)
    existing = [p for p in candidates if _path_exists_safe(p)]
    return existing if existing else candidates


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
                t = item.get("thinking")
                if isinstance(t, str) and t.strip():
                    chunks.append(t.strip())
            elif typ == "tool_use":
                name = item.get("name", "")
                tool_input = item.get("input")
                if isinstance(tool_input, dict):
                    arg = json.dumps(tool_input, ensure_ascii=False)
                else:
                    arg = str(tool_input or "")
                chunks.append(f"[tool_use] {name} {arg}".strip())
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


def _to_windows_path_display(path_str: str) -> str:
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


def _project_display_label(raw_project: str, cwd: str) -> str:
    if isinstance(cwd, str) and cwd.strip():
        return _to_windows_path_display(cwd)
    return _decode_project_slug_to_windows_path(raw_project)


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
    search_limit = 2500
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

    summary["project"] = _project_display_label(summary["project"], summary["cwd"])
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
        summary["search_text"] = " ".join(texts[:20])
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
            events.append({"timestamp": ts, "kind": kind, "role": role, "text": text})
            if len(events) >= MAX_EVENTS:
                break
    return {"events": events, "raw_line_count": raw_count}


def load_desktop_events(path: Path):
    events = []
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
.ev.kind-message { box-shadow: inset 0 0 0 1px rgba(20, 90, 160, 0.08); }
.ev.kind-queue { border-left-color: #a855f7; background: #f5efff; }
.ev.kind-progress { border-left-color: #f59e0b; background: #fff7e6; }
.ev.kind-notice { border-left-color: #0ea5e9; background: #eaf7ff; }
.ev.kind-system { border-left-color: #64748b; background: #f1f5f9; }
.ev-head {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 8px;
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
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
pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
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
      <label><input type="checkbox" id="only_user_instruction" /> ユーザー発話のみ表示</label>
      <label><input type="checkbox" id="reverse_order" /> 表示順を逆にする</label>
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
  const terms = q.split(new RegExp('\\s+')).filter(Boolean);

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
  if(document.getElementById('only_user_instruction').checked){
    events = events.filter(ev => ev.role === 'user');
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
    return;
  }

  const displayEvents = getDisplayEvents();
  const sourceType = state.activeSession.source_type || '';
  const sourceClass = sourceType === 'claude_cli' ? 'cli' : 'desktop';
  meta.innerHTML =
    `source: <code class="source-code ${sourceClass}">${esc(state.activeSession.source)}</code> | path: <code class="path-code">${esc(state.activeSession.relative_path)}</code> | ` +
    `project: <code class="project-code">${esc(state.activeSession.project || '-')}</code> | ` +
    `events: ${displayEvents.length}/${state.activeEvents.length} | raw lines/snippets: ${state.activeRawLineCount}`;

  eventsBox.innerHTML = displayEvents.map(ev => {
    const role = ev.role || 'system';
    const kind = ev.kind || 'event';
    const safeKind = String(kind).replace(/[^a-zA-Z0-9_-]/g, '-').toLowerCase();
    const body = `<pre>${esc(ev.text || '')}</pre>`;
    return `<div class="ev ${role} kind-${safeKind}"><div class="ev-head"><span>${esc(kind)}</span><span class="ev-role ${esc(role)}">${esc(role)}</span><span>${esc(fmt(ev.timestamp))}</span></div>${body}</div>`;
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
    return;
  }
  state.activeSession = data.session;
  state.activeEvents = data.events || [];
  state.activeRawLineCount = data.raw_line_count || 0;
  renderActiveSession();
}

document.getElementById('project_q').addEventListener('input', applyFilter);
document.getElementById('date_from').addEventListener('change', applyFilter);
document.getElementById('date_to').addEventListener('change', applyFilter);
document.getElementById('q').addEventListener('input', applyFilter);
document.getElementById('source_filter').addEventListener('change', applyFilter);
document.getElementById('mode').addEventListener('change', applyFilter);
document.getElementById('reload').addEventListener('click', loadSessions);
document.getElementById('only_user_instruction').addEventListener('change', renderActiveSession);
document.getElementById('reverse_order').addEventListener('change', renderActiveSession);
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
