#!/usr/bin/env python3
"""换取鉴X MCP 访问令牌，存到 Skill 旁 .jianx-auth.json。

不区分手机和电脑。始终：

  python oauth_login.py

脚本每次都在 stdout 里给出授权链接，并尝试打开系统浏览器。后台监听 127.0.0.1:8765/callback。
授权页不跳进本机。同意后页面先把授权送回这个地址，再停住给出一次性授权码。
告诉用户：在页面中弹出的页面复制授权码，复制完成后回到当前对话，粘贴授权码即可完成连接。
用户回到对话后先查本机有没有写成 token：

  python oauth_login.py --status

仍在等，且用户发回了授权码：

  python oauth_login.py --callback "<用户发回的授权码>"
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


SKILL_ROOT = Path(__file__).resolve().parents[1]
AUTH_PATH = SKILL_ROOT / ".jianx-auth.json"
PENDING_PATH = SKILL_ROOT / ".jianx-oauth-pending.json"
LISTENER_PATH = SKILL_ROOT / ".jianx-oauth-listener.json"

DEFAULT_RESOURCE = "https://chat-flow.turingsenseai.com/api/open/mcp"
DEFAULT_AS = "https://jianx-shechao.turingsenseai.com"
DEFAULT_AUTHORIZE = "https://jianx-chat.turingsenseai.com/apps/oauth/authorize"
REDIRECT_URI = "http://127.0.0.1:8765/callback"
LISTEN = ("127.0.0.1", 8765)
PENDING_TTL = timedelta(minutes=15)


def emit(obj: Dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pkce() -> Tuple[str, str]:
    verifier = b64url(secrets.token_bytes(32))
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def http_json(method: str, url: str, body: Optional[Dict[str, Any]] = None, form: bool = False) -> Dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError("HTTP %s %s: %s" % (exc.code, url, text[:500])) from exc


def register_client(as_base: str) -> str:
    try:
        out = http_json("POST", as_base.rstrip("/") + "/oauth/register", {
            "redirect_uris": [REDIRECT_URI],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "client_name": "WorkBuddy Appraisal",
        })
        cid = str(out.get("client_id") or "").strip()
        if cid:
            return cid
    except Exception:
        pass
    return "workbuddy"


def exchange(as_base: str, client_id: str, code: str, verifier: str, resource: str) -> Dict[str, Any]:
    return http_json(
        "POST",
        as_base.rstrip("/") + "/oauth/token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": resource,
        },
        form=True,
    )


def save_auth(payload: Dict[str, Any], meta: Dict[str, Any]) -> Path:
    expires_in = int(payload.get("expires_in") or 0)
    now = datetime.now(timezone.utc)
    expires_at = ""
    if expires_in > 0:
        expires_at = (now + timedelta(seconds=expires_in)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "access_token": payload.get("access_token"),
        "token_type": payload.get("token_type") or "Bearer",
        "expires_in": expires_in or payload.get("expires_in"),
        "expires_at": expires_at,
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": payload.get("scope"),
        "resource": meta.get("resource"),
        "client_id": meta.get("client_id"),
        "authorization_server": meta.get("as_base"),
    }
    AUTH_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return AUTH_PATH


def save_pending(data: Dict[str, Any]) -> None:
    PENDING_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_pending() -> Dict[str, Any]:
    if not PENDING_PATH.is_file():
        emit({
            "ok": False,
            "error": "no_pending",
            "message": "没有待完成的授权。先运行 python oauth_login.py --manual，再把用户发回的授权码交给 --callback。",
        })
        raise SystemExit(1)
    try:
        data = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        emit({"ok": False, "error": "bad_pending", "detail": str(exc)})
        raise SystemExit(1)
    created = parse_time(str(data.get("created_at") or ""))
    if created is not None and datetime.now(timezone.utc) - created > PENDING_TTL:
        emit({
            "ok": False,
            "error": "pending_expired",
            "message": "这轮授权已过期。重新运行 python oauth_login.py --manual，让用户重新同意。",
        })
        raise SystemExit(1)
    return data


def parse_time(raw: str) -> Optional[datetime]:
    text = raw.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_callback_input(raw: str) -> Tuple[str, str, str]:
    """从用户粘贴里取出 code、state、error。可以是整段回调 URL，或单独的授权码。"""
    text = raw.strip().strip('"').strip("'")
    if not text:
        return "", "", ""
    query = ""
    if "://" in text:
        parsed = urllib.parse.urlparse(text)
        query = parsed.query
        if not query and "?" in text:
            query = text.split("?", 1)[1]
    elif "code=" in text or "error=" in text:
        query = text.split("?", 1)[-1]
        code_at = query.find("code=")
        error_at = query.find("error=")
        cuts = [i for i in (code_at, error_at) if i >= 0]
        if cuts and min(cuts) > 0:
            query = query[min(cuts):]
    if query:
        qs = urllib.parse.parse_qs(query, keep_blank_values=True)
        code = (qs.get("code") or [""])[0].strip()
        state = (qs.get("state") or [""])[0].strip()
        error = (qs.get("error") or [""])[0].strip()
        if code or error:
            return code, state, error
    return text.split()[0].strip(), "", ""


def build_auth_url(authorize: str, client_id: str, state_val: str, challenge: str, resource: str) -> str:
    # verifier 只留在本机 pending 里。放进链接后，手机页面若自己换票，失败会把一次性授权码作废。
    return authorize + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": "mcp:invoke",
        "state": state_val,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": resource,
    })


def looks_like_access_token(text: str) -> bool:
    parts = text.split(".")
    return len(parts) == 3 and all(parts) and len(text) > 40 and "://" not in text


def extract_access_token(raw: str) -> str:
    for part in raw.replace("\n", " ").split():
        piece = part.strip().strip('"').strip("'").strip("，。,")
        if looks_like_access_token(piece):
            return piece
    return ""


def jwt_expiry(token: str) -> Optional[datetime]:
    try:
        payload = token.split(".")[1]
        pad = "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload + pad))
        exp = int(data.get("exp") or 0)
    except Exception:
        return None
    if exp <= 0:
        return None
    return datetime.fromtimestamp(exp, timezone.utc)


def read_pending_optional() -> Dict[str, Any]:
    if not PENDING_PATH.is_file():
        return {}
    try:
        data = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_pasted_token(token: str) -> int:
    pending = read_pending_optional()
    exp = jwt_expiry(token)
    now = datetime.now(timezone.utc)
    expires_in = 7 * 24 * 3600
    if exp is not None:
        expires_in = max(0, int((exp - now).total_seconds()))
    path = save_auth({
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": expires_in,
    }, {
        "resource": pending.get("resource") or DEFAULT_RESOURCE,
        "client_id": pending.get("client_id") or "",
        "as_base": pending.get("as_base") or DEFAULT_AS,
    })
    try:
        PENDING_PATH.unlink()
    except OSError:
        pass
    saved = json.loads(path.read_text(encoding="utf-8"))
    emit({
        "ok": True,
        "saved": str(path),
        "expires_in": expires_in,
        "expires_at": saved.get("expires_at"),
        "token_type": "Bearer",
        "has_access_token": True,
        "hint": "token 约 7 天过期，过期后重新授权。不要把 access_token 念出来。",
    })
    return 0


def finish_with_code(pending: Dict[str, Any], code: str) -> int:
    token = exchange(
        str(pending.get("as_base") or DEFAULT_AS),
        str(pending.get("client_id") or ""),
        code,
        str(pending.get("verifier") or ""),
        str(pending.get("resource") or DEFAULT_RESOURCE),
    )
    path = save_auth(token, {
        "resource": pending.get("resource"),
        "client_id": pending.get("client_id"),
        "as_base": pending.get("as_base"),
    })
    try:
        PENDING_PATH.unlink()
    except OSError:
        pass
    saved = json.loads(path.read_text(encoding="utf-8"))
    emit({
        "ok": True,
        "saved": str(path),
        "expires_in": token.get("expires_in"),
        "expires_at": saved.get("expires_at"),
        "token_type": token.get("token_type") or "Bearer",
        "has_access_token": bool(token.get("access_token")),
        "hint": "token 约 7 天过期，过期后重新授权。不要把 access_token 念出来。",
    })
    return 0


def complete_from_callback(raw: str) -> int:
    text = raw.strip()
    if "://" not in text and "code=" not in text:
        token = extract_access_token(text)
        if token:
            return save_pasted_token(token)
    pending = load_pending()
    code, state, error = parse_callback_input(raw)
    if error:
        emit({"ok": False, "error": error, "message": "用户没有同意授权。需要的话重新运行 oauth_login.py。"})
        return 1
    if not code:
        emit({
            "ok": False,
            "error": "missing_code",
            "message": "没有从用户发回的内容里找到授权码。请让用户发页面上的授权码，或地址栏整段链接。",
        })
        return 1
    expected = str(pending.get("state") or "")
    if state and expected and state != expected:
        emit({
            "ok": False,
            "error": "state_mismatch",
            "message": "这不是这一轮授权的回调。请用刚刚那条链接重新同意，或重新运行 oauth_login.py。",
        })
        return 1
    try:
        return finish_with_code(pending, code)
    except Exception as exc:
        emit({
            "ok": False,
            "error": "exchange_failed",
            "detail": str(exc),
            "message": "授权码换 token 失败。授权码大约 5 分钟有效，过期了就重新运行 oauth_login.py。",
        })
        return 1


DONE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>授权完成</title>
<style>body{font-family:system-ui,sans-serif;max-width:420px;margin:12vh auto;padding:24px}
.ok{color:#0a7;font-size:22px;font-weight:600}</style></head><body>
<p class="ok">授权已完成</p>
<p>可以关掉这个标签，回到 WorkBuddy。</p>
</body></html>
"""

ERR_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>授权失败</title></head>
<body><p>授权失败：__ERR__</p></body></html>
"""


class AuthState:
    def __init__(self) -> None:
        self.code: Optional[str] = None
        self.error: Optional[str] = None
        self.event = threading.Event()


def make_handler(state: AuthState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _cors(self) -> None:
            # 授权页在 https 上。浏览器访问 127.0.0.1 前会先做预检，缺这些头会把本机误判成连不上。
            origin = self.headers.get("Origin") or "*"
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Vary", "Origin")

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/ping":
                self.send_response(204)
                self._cors()
                self.end_headers()
                return
            if parsed.path == "/callback":
                qs = urllib.parse.parse_qs(parsed.query)
                if qs.get("error"):
                    state.error = qs.get("error", ["unknown"])[0]
                    body = ERR_HTML.replace("__ERR__", state.error).encode("utf-8")
                    self.send_response(400)
                else:
                    state.code = (qs.get("code") or [""])[0]
                    body = DONE_HTML.encode("utf-8")
                    self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                state.event.set()
                return
            self.send_response(404)
            self._cors()
            self.end_headers()

    return Handler


def bind_server(state: AuthState) -> HTTPServer:
    # 禁止 SO_REUSEADDR，避免 Windows 上多个进程同时听 8765，回调打到旧进程。
    class ExclusiveHTTPServer(HTTPServer):
        allow_reuse_address = False

    try:
        return ExclusiveHTTPServer(LISTEN, make_handler(state))
    except OSError as exc:
        emit({
            "ok": False,
            "error": "port_in_use",
            "message": "8765 已被占用（多半是上次 oauth_login 没退干净）。请结束占用进程后重试。",
            "detail": str(exc),
            "hint": "Windows: netstat -ano | findstr :8765  然后 taskkill /PID <pid> /F",
        })
        raise SystemExit(1) from exc


def write_listener(status: str, extra: Optional[Dict[str, Any]] = None) -> None:
    data: Dict[str, Any] = {
        "pid": os.getpid(),
        "status": status,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if extra:
        data.update(extra)
    LISTENER_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def command_line(pid: int) -> str:
    if pid <= 0:
        return ""
    if os.name == "nt":
        try:
            out = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    '(Get-CimInstance Win32_Process -Filter "ProcessId=%d").CommandLine' % pid,
                ],
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
            return out.decode("utf-8", "replace")
        except Exception:
            return ""
    try:
        raw = Path("/proc/%d/cmdline" % pid).read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace")


def stop_previous_listener() -> None:
    if not LISTENER_PATH.is_file():
        return
    try:
        info = json.loads(LISTENER_PATH.read_text(encoding="utf-8"))
        pid = int(info.get("pid") or 0)
    except Exception:
        return
    if pid <= 0 or pid == os.getpid():
        return
    line = command_line(pid)
    if "oauth_login.py" not in line or "--serve" not in line:
        return
    if os.name == "nt":
        subprocess.call(
            ["taskkill", "/PID", str(pid), "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(0.4)


def wait_listener(timeout: float = 2.0) -> Dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if LISTENER_PATH.is_file():
            try:
                data = json.loads(LISTENER_PATH.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if isinstance(data, dict) and data.get("status") in ("listening", "error"):
                return data
        time.sleep(0.05)
    return {"status": "error", "error": "listener_start_timeout"}


def open_system_browser(url: str) -> bool:
    """尽量弹出系统浏览器。调用方无论成败都要把 url 发给用户。

    Windows 上 webbrowser.open 常返回 True，但浏览器还挂在当前任务里，
    脚本一退出窗口就没了。这里让浏览器脱离当前进程再打开。
    """
    if os.name == "nt":
        # start 的第一个引号参数是窗口标题，URL 单独成参，避免 & 被 cmd 拆开。
        commands = (
            ["cmd", "/c", "start", "", url],
            ["explorer", url],
        )
        flag_sets = (
            0x01000000 | 0x00000008 | 0x00000200,  # BREAKAWAY_FROM_JOB | DETACHED | NEW_GROUP
            0x00000008 | 0x00000200,
        )
        for command in commands:
            for flags in flag_sets:
                try:
                    subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        close_fds=True,
                        creationflags=flags,
                    )
                    return True
                except OSError:
                    continue
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    try:
        return bool(webbrowser.open(url, new=2, autoraise=True))
    except Exception:
        return False


def start_background_listener() -> Dict[str, Any]:
    stop_previous_listener()
    try:
        LISTENER_PATH.unlink()
    except OSError:
        pass
    cmd = [sys.executable, str(Path(__file__).resolve()), "--serve"]
    kwargs: Dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)
    return wait_listener(5)


def serve() -> int:
    try:
        pending = load_pending()
    except SystemExit:
        write_listener("error", {"error": "no_pending"})
        return 1
    state = AuthState()
    try:
        server = bind_server(state)
    except SystemExit:
        write_listener("error", {"error": "port_in_use"})
        return 1
    write_listener("listening")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if not state.event.wait(timeout=600):
        server.shutdown()
        write_listener("timeout")
        return 1
    server.shutdown()
    if state.error or not state.code:
        write_listener("error", {"error": state.error or "no_code"})
        return 1
    try:
        code = finish_with_code(pending, state.code)
    except Exception:
        write_listener("error", {"error": "exchange_failed"})
        return 1
    write_listener("saved")
    return code


def report_status() -> int:
    pending = read_pending_optional()
    pending_at = parse_time(str(pending.get("created_at") or ""))
    if AUTH_PATH.is_file():
        try:
            saved = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            emit({"ok": False, "error": "bad_token_file", "detail": str(exc)})
            return 1
        issued = parse_time(str(saved.get("issued_at") or ""))
        fresh = bool(saved.get("access_token")) and (
            pending_at is None or (issued is not None and issued >= pending_at)
        )
        if fresh:
            emit({
                "ok": True,
                "saved": str(AUTH_PATH),
                "expires_at": saved.get("expires_at"),
                "has_access_token": True,
                "hint": "授权已完成。不要把 access_token 念出来。",
            })
            return 0
    emit({
        "ok": False,
        "error": "waiting_for_user",
        "hint": "本机还没收下。请用户按页面复制授权码，回到当前对话粘贴，再运行 --callback。不要念出授权码。",
    })
    return 1


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", default=DEFAULT_RESOURCE)
    parser.add_argument("--as", dest="as_base", default=DEFAULT_AS)
    parser.add_argument("--authorize", default=DEFAULT_AUTHORIZE)
    parser.add_argument("--open", action="store_true", help="自动打开浏览器到授权长链接")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--manual", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--status", action="store_true", help="查看这一轮授权是否已经写入 token")
    parser.add_argument("--callback", default="", help="用户粘贴的回调整段，或 MCP 令牌")
    args = parser.parse_args()

    if args.serve:
        return serve()
    if args.callback.strip():
        return complete_from_callback(args.callback)
    if args.status:
        return report_status()

    verifier, challenge = pkce()
    state_val = secrets.token_urlsafe(12)
    client_id = register_client(args.as_base)
    auth_url = build_auth_url(args.authorize, client_id, state_val, challenge, args.resource)
    save_pending({
        "client_id": client_id,
        "verifier": verifier,
        "state": state_val,
        "redirect_uri": REDIRECT_URI,
        "resource": args.resource,
        "as_base": args.as_base,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    started = start_background_listener()
    opened = open_system_browser(auth_url)
    user_hint = "在页面中弹出的页面复制授权码，复制完成后回到当前对话，粘贴授权码即可完成连接。"
    hint = "无论 opened_browser 是真是假，都把 url 原样发给用户，并原样带上 user_hint。不要改链接，不要因为以为浏览器已打开就省略链接。授权页会先尝试把授权送回本机，地址栏不跳走。用户回到对话后先运行 python oauth_login.py --status。ok 为真就结束。仍在等且用户发回了授权码，再运行 --callback。5 分钟内有效，只能用一次。本机已收下时不要再换一次码。不要念出授权码或 token。"
    if started.get("status") != "listening":
        emit({
            "ok": False,
            "action": "send_authorize_url",
            "opened_browser": opened,
            "url": auth_url,
            "error": started.get("error") or "listener_failed",
            "message": "本机回调没有听上 8765。链接仍然有效，请发给用户。结束占用进程后可重试。",
            "user_hint": user_hint,
            "hint": hint + " Windows: netstat -ano | findstr :8765  然后 taskkill /PID <pid> /F",
        })
        return 1
    emit({
        "ok": True,
        "action": "send_authorize_url",
        "opened_browser": opened,
        "url": auth_url,
        "user_hint": user_hint,
        "hint": hint,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
