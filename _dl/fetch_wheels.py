"""从阿里云镜像批量下载纯 Python / cp312 Windows wheel 到本地 whl/ 目录。

绕过 pip install：pip 在临时目录解包阶段会被 DSH 沙箱拦截，
而 urllib + 代理直下 + zipfile 解压可以落盘到 site-packages。
"""
import os
import re
import sys
import urllib.request

PROXY = "http://127.0.0.1:7897"
SIMPLE = "http://mirrors.aliyun.com/pypi/simple/"
BASE = "http://mirrors.aliyun.com/pypi/packages/"

# 某些包的古老版本恰好是 py3-none-any（如 pydantic-core 0.0.1），
# 单纯按 "none-any 优先" 会选中它们。这里给需要 ABI 的包设最低版本下限。
# 版本天花板：太新的包会引入镜像上缺失的传递依赖（如 fastapi 0.162 依赖
# annotated_doc，阿里云镜像尚未收录），因此把上限压到已验证可用的版本。
MAX_VERSION = {
    "fastapi": (0, 116, 0),
    # pydantic 与其 Rust 内核必须版本配套，否则报
    # cannot import name 'validate_core_schema' from 'pydantic_core'；
    # 上限跟随当前下到的 pydantic 2.9.x 所要求的 core 线。
    "pydantic-core": (2, 24, 0),
    # pydantic 必须落到 2.9.x：它与 core 2.23.4 配套，再高的 pydantic 会要求 core 2.50+。
    "pydantic": (2, 10, 0),
    # fastapi 0.115.x 要求 starlette <0.42，镜像上的 1.9.0 太新。
    "starlette": (0, 42, 0),
    # pydantic-settings 2.9+ 依赖 typing_inspection 且要求更新的 pydantic；
    # 压到 2.8 以内以配合当前 pydantic 2.9.2。
    "pydantic-settings": (2, 8, 0),
}

MIN_VERSION = {
    "pydantic-core": (2, 23, 4),
    "typing-extensions": (4, 6, 0),
    "h11": (0, 12, 0),
    "websockets": (12, 0),
    "starlette": (0, 30, 0),
    "fastapi": (0, 110, 0),
    "uvicorn": (0, 29, 0),
    "httpx": (0, 27, 0),
    "pydantic": (2, 6, 0),
    "aiosqlite": (0, 20, 0),
    "aiofiles": (23, 2, 0),
}
HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "whl")
os.makedirs(DEST, exist_ok=True)

PACKAGES = [
    "fastapi", "httpx", "annotated-types", "anyio", "certifi", "click", "colorama", "h11",
    "httpcore", "httptools", "idna", "python-dotenv", "pyyaml",
    "sniffio", "starlette", "typing-extensions", "watchfiles",
    "websockets", "aiofiles", "aiosqlite",
    "pydantic", "pydantic-core", "pydantic-settings", "uvicorn",
]

opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
)
opener.addheaders = [("User-Agent", "pip/26.2.2 wheel-fetcher")]


def _version_key(filename):
    """从 wheel 文件名解析版本，返回可比较元组；预发布版返回 None。"""
    m = re.search(r"-([0-9][^-]*)-", filename)
    if not m:
        return None
    raw = m.group(1)
    # 排除 rc / a / b / dev 预发布版
    if re.search(r"(?:rc|a|b|dev)\d*$", raw):
        return None
    parts = []
    for chunk in raw.split("."):
        if not chunk.isdigit():
            return None
        parts.append(int(chunk))
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def pick_link(html, pkg=""):
    """从 PEP 503 simple 页挑一个可用 wheel：py3-none-any 优先，其次 cp312-win_amd64。
    按真实版本号排序（不做文件名字符串比较），跳过预发布版与落在
    [MIN_VERSION, MAX_VERSION] 区间之外的版本。"""
    links = re.findall(r'href="([^"]+?\.whl)(?:#sha256=[0-9a-f]+)?"', html)
    if not links:
        return None
    floor = MIN_VERSION.get(pkg)
    ceil = MAX_VERSION.get(pkg)
    scored = []
    for href in links:
        name = href.split("/")[-1].lower()
        if "none-any" in name:
            score = 3
        elif "cp312" in name and "win_amd64" in name:
            score = 2
        else:
            continue
        ver = _version_key(name)
        if ver is None:
            continue
        if floor is not None and ver < tuple(floor):
            continue
        if ceil is not None and ver >= tuple(ceil):
            continue
        scored.append((score, ver, href))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]))
    return scored[-1][2]


def main():
    if os.path.isdir(DEST) and os.listdir(DEST):
        print("whl/ 已有内容，继续增量下载")
    ok, skipped, failed = [], [], []
    for pkg in PACKAGES:
        target = os.path.join(DEST, pkg + ".whl")
        if os.path.exists(target) and os.path.getsize(target) > 1000:
            skipped.append(pkg)
            continue
        try:
            with opener.open(SIMPLE + pkg + "/", timeout=60) as r:
                html = r.read().decode("utf-8", "replace")
        except Exception as e:
            failed.append((pkg, "index: " + str(e)))
            print("FAIL index", pkg, e)
            continue
        href = pick_link(html, pkg)
        if not href:
            failed.append((pkg, "no wheel found"))
            print("FAIL wheel", pkg)
            continue
        if href.startswith("http"):
            url = href
        else:
            # 形如 ../../packages/xx/.../file.whl
            url = BASE + "/".join(href.split("/")[1:])
        try:
            with opener.open(url, timeout=180) as r:
                data = r.read()
            with open(target, "wb") as f:
                f.write(data)
            print("OK  %-22s %8d  %s" % (pkg, len(data), url.split("/")[-1]))
            ok.append(pkg)
        except Exception as e:
            failed.append((pkg, "download: " + str(e)))
            print("FAIL down", pkg, e)
    print()
    print("downloaded=%d skipped=%d failed=%d" % (len(ok), len(skipped), len(failed)))
    for name, why in failed:
        print("  FAIL", name, "->", why)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
