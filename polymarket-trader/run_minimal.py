"""启动脚本：借用 DSH 运行时 Python + 项目 venv 的 site-packages。

背景：本机沙箱网络不通（PyPI 超时），无法为项目 .venv 补装 uvicorn；
而 DSH 自带运行时 Python 与 .venv 同为 3.12，只需把 .venv 的 site-packages
注入 sys.path 即可复用已装好的全部依赖。

uvicorn 本身缺失，但 FastAPI 应用可通过任意 ASGI 服务器启动；
本脚本先用纯标准库实现一个最小 ASGI 服务器，再挂载 create_app()。

用法:
    python run_minimal.py                     # 仅本机访问 (127.0.0.1:8765)
    python run_minimal.py --host 0.0.0.0      # 允许手机/局域网设备访问
    python run_minimal.py --port 9000         # 自定义端口
    python run_minimal.py --no-browser        # 不自动打开浏览器
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_SITE = PROJECT_ROOT / ".venv" / "Lib" / "site-packages"

# Windows 控制台默认 GBK 编码，任何非 ASCII 字符（尤其是 emoji）都会让 print()
# 抛 UnicodeEncodeError 并导致进程退出。这里强制 stdout/stderr 为 UTF-8 且容错，
# 保证双击 .bat 启动时不会因为一行提示文字而崩溃。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 把项目 venv 的依赖注入 sys.path（最优先）
if VENV_SITE.is_dir():
    sys.path.insert(0, str(VENV_SITE))

os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from backend.main import create_app  # noqa: E402

HOST = os.environ.get("PM_HOST", "127.0.0.1")
PORT = int(os.environ.get("PM_PORT", "8765"))


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, app) -> None:
    """最小 HTTP/1.1 处理：支持 GET/POST/PUT/DELETE + 静态文件 + WebSocket 升级。"""
    try:
        request_line = await reader.readline()
        if not request_line:
            writer.close()
            return
        parts = request_line.decode("latin-1").strip().split()
        if len(parts) < 3:
            writer.close()
            return
        method, raw_path, _ = parts

        # 拆出 path 与 query_string（FastAPI 依赖 query_string 解析查询参数）
        path_only, _, query_string = raw_path.partition("?")

        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            k, _, v = line.decode("latin-1").partition(":")
            headers[k.strip().lower()] = v.strip()

        # WebSocket 升级：交给 uvicorn 缺失时的降级提示
        if headers.get("upgrade", "").lower() == "websocket":
            writer.write(
                b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
            writer.close()
            return

        # 读请求体
        body = b""
        clen = int(headers.get("content-length", "0") or 0)
        if clen > 0:
            body = await reader.readexactly(clen)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path_only,
            "raw_path": raw_path.encode("latin-1"),
            "query_string": query_string.encode("latin-1"),
            "root_path": "",
            "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
            "client": (HOST, 0),
            "server": (HOST, PORT),
        }

        sent = {"done": False}

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                sent["status"] = message["status"]
                sent["headers"] = list(message.get("headers", []))
                # 确保声明 UTF-8 字符集，避免浏览器按 GBK 解码中文
                if not any(k.lower() == b"content-type" for k, _ in sent["headers"]):
                    sent["headers"].append((b"content-type", b"application/json; charset=utf-8"))
            elif message["type"] == "http.response.body":
                if not sent.get("_started"):
                    hdrs = b"".join(
                        b"%s: %s\r\n" % (k, v) for k, v in sent.get("headers", [])
                    )
                    # 累积所有 body chunk，最后一次性写出（保证 Content-Length 准确）
                    sent.setdefault("_chunks", []).append(message.get("body", b""))
                    if not message.get("more_body", False):
                        payload = b"".join(sent["_chunks"])
                        writer.write(
                            b"HTTP/1.1 %d OK\r\n%sContent-Length: %d\r\nConnection: close\r\n\r\n"
                            % (sent.get("status", 200), hdrs, len(payload))
                        )
                        writer.write(payload)
                        await writer.drain()
                    sent["_started"] = True
                    return
                writer.write(message.get("body", b""))
                await writer.drain()

        await app(scope, receive, send)
        if not sent.get("_started"):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
        elif sent.get("_chunks") and len(sent["_chunks"]) > 1:
            pass  # 多 chunk 已在 send() 内拼接写出
        writer.close()
    except Exception:
        try:
            writer.close()
        except Exception:
            pass


def _parse_args() -> argparse.Namespace:
    """解析命令行参数（与 run.py 保持一致的 --host/--port/--no-browser）。"""
    parser = argparse.ArgumentParser(
        description="Polymarket Trader 降级启动器（无 uvicorn 时使用，不支持 WebSocket 推送）"
    )
    parser.add_argument("--host", default=os.environ.get("PM_HOST", "127.0.0.1"),
                        help="监听地址；需要手机/其他设备访问时必须填 0.0.0.0 (默认: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PM_PORT", "8765")),
                        help="监听端口 (默认: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径 (默认: config.yaml)")
    return parser.parse_args()


async def main() -> None:
    global HOST, PORT
    args = _parse_args()
    HOST = args.host
    PORT = args.port

    # 配置文件路径透传给 config 模块
    os.environ.setdefault("CONFIG_FILE", args.config)

    app = create_app()

    # 手动执行 lifespan 启动阶段（uvicorn 缺失时它不会自动跑），
    # 否则 StateStore / 执行器 / 监控器等均未初始化，相关端点会 500。
    startup_handlers = app.router.on_startup
    for handler in startup_handlers:
        await handler()
    # FastAPI 0.110+ 改用 lifespan 协议：直接进入 lifespan 上下文
    if not startup_handlers and app.router.lifespan_context is not None:
        ctx = app.router.lifespan_context(app)
        await ctx.__aenter__()

    # 0.0.0.0 表示监听所有网卡，局域网内其他设备（手机/平板/另一台电脑）
    # 才能通过 <本机局域网IP>:<端口> 访问。
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, app), HOST, PORT
    )
    # 注意：Windows 控制台默认 GBK，直接 print emoji 会抛 UnicodeEncodeError
    # 导致进程崩溃（表现为"双击启动后窗口一闪就没"）。这里统一用 ASCII 输出。
    print(f"[OK] Service started: http://{HOST}:{PORT}", flush=True)
    if HOST in ("0.0.0.0", "::"):
        print(f"[INFO] LAN access: open http://<your-LAN-IP>:{PORT} on your phone "
              f"(run ipconfig to find the IPv4 address)", flush=True)
    print("[INFO] WebSocket push is unavailable in degraded mode (uvicorn missing); "
          "data updates via polling.", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
