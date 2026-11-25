#!/usr/bin/env python3
"""用 Skill 旁保存的 MCP token 调统一 MCP 地址。

用法：
  python mcp_call.py tools/list
  python mcp_call.py tools/call create_uploads '{"items":[{"contentType":"image/jpeg"}]}'
  python mcp_call.py --reply tools/call appraise_points '...'
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


SKILL_ROOT = Path(__file__).resolve().parents[1]
AUTH_PATH = SKILL_ROOT / ".jianx-auth.json"
DEFAULT_MCP = "https://chat-flow.turingsenseai.com/api/open/mcp"
# 打包正式包时改成 pro。工作流靠这个值选择鉴X、鉴宝、鉴玉和算法域名。
API_ENV = "pro"
WORKFLOW_ENV_TOOLS = {
    "appraise_point",
    "appraise_points",
    "appraise_images",
    "appraise_overall",
    "appraise_nopointer",
    "recognize_style",
}


def emit(obj: Dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def parse_time(raw: str) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_auth() -> Dict[str, Any]:
    if not AUTH_PATH.exists():
        emit({
            "ok": False,
            "error": "missing_token",
            "hint": "运行 python scripts/oauth_login.py。告诉用户：在页面中弹出的页面复制授权码，复制完成后回到当前对话，粘贴授权码即可完成连接。用户回来先 --status，仍在等再把授权码交给 --callback。token 约 7 天有效。",
        })
        raise SystemExit(1)
    try:
        return json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        emit({"ok": False, "error": "bad_token_file", "detail": str(exc)})
        raise SystemExit(1)


def ensure_fresh(auth: Dict[str, Any]) -> None:
    expires_at = parse_time(str(auth.get("expires_at") or ""))
    if expires_at is None:
        # 兼容旧文件：有 expires_in + issued_at 时推算；都没有则放行，等 401。
        issued = parse_time(str(auth.get("issued_at") or ""))
        try:
            expires_in = int(auth.get("expires_in") or 0)
        except Exception:
            expires_in = 0
        if issued is not None and expires_in > 0:
            from datetime import timedelta
            expires_at = issued + timedelta(seconds=expires_in)
    if expires_at is None:
        return
    now = datetime.now(timezone.utc)
    if now >= expires_at:
        emit({
            "ok": False,
            "error": "expired",
            "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hint": "token 约 7 天过期。重新运行 python scripts/oauth_login.py。",
        })
        raise SystemExit(1)


def rpc(method: str, params: Optional[Dict[str, Any]] = None, req_id: int = 1) -> Dict[str, Any]:
    auth = load_auth()
    ensure_fresh(auth)
    token = str(auth.get("access_token") or "").strip()
    if not token:
        emit({"ok": False, "error": "empty_token"})
        raise SystemExit(1)
    url = str(auth.get("resource") or DEFAULT_MCP).strip()
    body = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        out: Dict[str, Any] = {"ok": False, "status": exc.code, "body": text[:1000]}
        if exc.code in (401, 403):
            out["error"] = "unauthorized"
            out["hint"] = "token 失效（约 7 天过期）。重新运行 python scripts/oauth_login.py。"
        emit(out)
        raise SystemExit(1)


def flatten_reply(raw: Dict[str, Any]) -> Any:
    """result.content[0].text → outputs.reply → JSON。"""
    try:
        result = raw.get("result") if isinstance(raw, dict) else None
        if not isinstance(result, dict):
            return raw
        content = result.get("content")
        text = ""
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                text = str(first.get("text") or "")
        if not text.strip():
            return result
        try:
            parsed = json.loads(text)
        except Exception:
            return {"text": text}
        if isinstance(parsed, dict) and isinstance(parsed.get("outputs"), dict):
            reply = parsed["outputs"].get("reply")
            if isinstance(reply, str) and reply.strip():
                try:
                    return json.loads(reply)
                except Exception:
                    return {"reply": reply}
            return parsed.get("outputs")
        return parsed
    except Exception:
        return raw


def business_ok(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return True
    status = str(payload.get("status") or "").strip().lower()
    if not status:
        return True
    return status in ("success",)


def with_api_env(name: str, arguments: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    args = dict(arguments or {})
    if name in WORKFLOW_ENV_TOOLS:
        args["api_env"] = API_ENV
    return args


def call_tool(name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
    raw = rpc("tools/call", {"name": name, "arguments": with_api_env(name, arguments)})
    return flatten_reply(raw)


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--reply", action="store_true", help="扁平输出 outputs.reply / content text")
    parser.add_argument("method")
    parser.add_argument("rest", nargs="*")
    args = parser.parse_args()

    method = args.method
    rest = args.rest
    if method == "tools/list":
        raw = rpc("tools/list")
        emit(flatten_reply(raw) if args.reply else raw)
        return 0
    if method == "tools/call":
        if len(rest) < 1:
            print("usage: mcp_call.py [--reply] tools/call <name> [json-args]", file=sys.stderr)
            return 2
        name = rest[0]
        call_args = json.loads(rest[1]) if len(rest) > 1 else {}
        raw = rpc("tools/call", {"name": name, "arguments": with_api_env(name, call_args)})
        if args.reply:
            flat = flatten_reply(raw)
            emit(flat if isinstance(flat, dict) else {"data": flat})
            return 0 if business_ok(flat) else 1
        emit(raw)
        return 0
    raw = rpc(method)
    emit(flatten_reply(raw) if args.reply else raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
