#!/usr/bin/env python3
"""Polymarket Trader 一键启动脚本。

功能：
  - 检查 Python 版本（≥ 3.10）
  - 检查关键依赖是否已安装（缺失则提示 pip install）
  - 首次运行自动从 config.example.yaml 复制 config.yaml
  - 启动 uvicorn 服务
  - 自动打开浏览器到 http://localhost:{port}
  - Ctrl+C 优雅关闭

用法:
    python run.py                          # 使用默认配置
    python run.py --config config.yaml     # 指定配置文件
    python run.py --host 127.0.0.1         # 指定监听地址
    python run.py --port 9000              # 指定监听端口
    python run.py --reload                 # 开发模式热重载
    python run.py --no-browser             # 不自动打开浏览器
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── 常量 ──────────────────────────────────────────────────────

MIN_PYTHON = (3, 10)

# Windows 控制台默认 GBK 编码，非 ASCII 字符（含 emoji）会让 print() 抛
# UnicodeEncodeError 并导致进程退出。强制 UTF-8 且容错，保证双击启动不崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 启动时检查的关键依赖包（import 名 → pip 包名）
REQUIRED_PACKAGES: list[tuple[str, str]] = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn[standard]"),
    ("httpx", "httpx"),
    ("pydantic", "pydantic"),
    ("pydantic_settings", "pydantic-settings"),
    ("yaml", "pyyaml"),
    ("aiosqlite", "aiosqlite"),
]

# 可选依赖（缺失只警告不阻断）
OPTIONAL_PACKAGES: list[tuple[str, str]] = [
    ("py_clob_client", "py-clob-client"),  # 实盘交易需要
]


# ── 检查函数 ──────────────────────────────────────────────────

def check_python_version() -> None:
    """检查 Python 版本是否满足最低要求。"""
    if sys.version_info < MIN_PYTHON:
        print(f"❌ Python 版本过低：当前 {sys.version_info[0]}.{sys.version_info[1]}，"
              f"需要 ≥ {MIN_PYTHON[0]}.{MIN_PYTHON[1]}")
        sys.exit(1)
    print(f"✅ Python {sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")


def check_dependencies() -> None:
    """检查关键依赖是否已安装，缺失则提示安装。"""
    missing: list[str] = []

    for import_name, pip_name in REQUIRED_PACKAGES:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)

    if missing:
        print(f"❌ 缺少依赖：{', '.join(missing)}")
        print()
        print("请安装依赖后重试：")
        print(f"  pip install {' '.join(missing)}")
        print()
        answer = input("是否现在自动安装？(y/N): ").strip().lower()
        if answer == "y":
            cmd = [sys.executable, "-m", "pip", "install"] + missing
            print(f"运行: {' '.join(cmd)}")
            result = subprocess.run(cmd)
            if result.returncode != 0:
                print("❌ 安装失败，请手动安装后重试。")
                sys.exit(1)
            print("✅ 依赖安装成功")
        else:
            sys.exit(1)
    else:
        print("✅ 所有关键依赖已安装")

    # 可选依赖检查
    for import_name, pip_name in OPTIONAL_PACKAGES:
        try:
            __import__(import_name)
        except ImportError:
            print(f"⚠️  可选依赖 {pip_name} 未安装（实盘交易功能不可用）")


def ensure_config(project_root: Path, config_arg: str) -> str:
    """确保配置文件存在，首次运行自动从 example 复制。

    Args:
        project_root: 项目根目录
        config_arg: 命令行指定的配置文件路径

    Returns:
        实际使用的配置文件路径
    """
    config_path = Path(config_arg)
    if config_path.is_absolute():
        target = config_path
    else:
        target = project_root / config_arg

    if target.exists():
        print(f"✅ 配置文件: {target}")
        return str(target)

    # 尝试从 example 复制
    example = project_root / "config.example.yaml"
    if example.exists():
        shutil.copy2(example, target)
        print(f"✅ 已从 config.example.yaml 创建 {target}")
        return str(target)

    # 没有 example 也没有 config，使用默认值继续
    print(f"⚠️  配置文件 {target} 不存在，将使用默认配置")
    return str(target)


def open_browser(url: str, delay: float = 1.5) -> None:
    """延迟后自动打开浏览器。"""
    def _open():
        time.sleep(delay)
        try:
            import webbrowser
            webbrowser.open(url)
            print(f"🌐 浏览器已打开: {url}")
        except Exception:
            print(f"⚠️  无法自动打开浏览器，请手动访问: {url}")

    thread = threading.Thread(target=_open, daemon=True)
    thread.start()


# ── 主函数 ────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Polymarket Trader — 实时监测 Polymarket 钱包交易、多策略可组合跟单系统"
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="配置文件路径 (默认: config.yaml)"
    )
    parser.add_argument("--host", default=None, help="监听地址 (覆盖配置)")
    parser.add_argument("--port", type=int, default=None, help="监听端口 (覆盖配置)")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    # 确保项目根目录在 sys.path 中
    project_root = Path(__file__).resolve().parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    print("=" * 60)
    print("  Polymarket Trader")
    print("  实时监测 · 多策略跟单 · Polymarket")
    print("=" * 60)

    # 1. 检查 Python 版本
    check_python_version()

    # 2. 检查依赖
    check_dependencies()

    # 3. 确保配置文件存在
    config_path = ensure_config(project_root, args.config)

    # 4. 设置环境变量供 config 模块读取
    os.environ["CONFIG_FILE"] = config_path

    # 5. 加载配置
    from backend.config import get_settings
    settings = get_settings()
    host = args.host or settings.server.host
    port = args.port or settings.server.port

    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    print()
    print(f"🚀 启动服务: {url}")
    print(f"   配置文件: {config_path}")
    print(f"   执行模式: {settings.executor.mode}")
    print(f"   监控间隔: {settings.monitor.interval}s")
    print(f"   数据库:   {settings.db_path}")
    print(f"   前端目录: {settings.frontend_dir}")
    print()

    # 6. 自动打开浏览器
    if not args.no_browser:
        open_browser(url)

    # 7. 启动 uvicorn
    import uvicorn

    try:
        uvicorn.run(
            "backend.main:app",
            host=host,
            port=port,
            reload=args.reload,
            reload_dirs=[str(project_root / "backend")] if args.reload else None,
            log_level="info",
        )
    except KeyboardInterrupt:
        print()
        print("👋 收到 Ctrl+C，正在关闭...")
    finally:
        print("👋 Polymarket Trader 已停止")


if __name__ == "__main__":
    main()
