"""把 whl/ 目录下所有 wheel 解压安装到嵌入式 Python 的 site-packages。

依赖解析已在下载阶段人工完成（requirements.txt 的传递依赖均已列入 PACKAGES），
这里只做纯解压 + 安装可执行脚本包装。
"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
WHL = os.path.join(HERE, "whl")
SP = r"D:\Users\Administrator\Documents\deepseek-harness\default-workspace\Python312\Lib\site-packages"
SCRIPTS = r"D:\Users\Administrator\Documents\deepseek-harness\default-workspace\Python312\Scripts"

os.makedirs(SP, exist_ok=True)
os.makedirs(SCRIPTS, exist_ok=True)

wheels = sorted(
    f for f in os.listdir(WHL)
    if f.lower().endswith((".whl", ".zip"))
)
if not wheels:
    print("whl/ 为空，无 wheel 可安装")
    sys.exit(1)

installed = 0
for name in wheels:
    path = os.path.join(WHL, name)
    try:
        with zipfile.ZipFile(path) as z:
            members = [m for m in z.namelist() if not m.startswith("Scripts/")]
            z.extractall(SP, members=members)
            # uvicorn 这类自带控制台脚本，补regenerate .exe-less 启动入口
            scripts = [m for m in z.namelist() if m.startswith("Scripts/")]
            for s in scripts:
                data = z.read(s)
                out = os.path.join(SCRIPTS, s.split("/", 1)[1])
                with open(out, "wb") as f:
                    f.write(data)
        print("INSTALLED %-28s" % name)
        installed += 1
    except Exception as e:
        print("FAIL %s -> %s" % (name, e))

print()
print("installed=%d/%d -> %s" % (installed, len(wheels), SP))

# 校验关键模块能否导入
print()
print("=== import check ===")
bad = 0
for m in ["fastapi", "uvicorn", "httpx", "pydantic", "yaml",
          "websockets", "aiofiles", "aiosqlite"]:
    try:
        __import__(m)
        print("OK   %s" % m)
    except Exception as e:
        print("FAIL %s -> %s" % (m, e))
        bad += 1

try:
    import starlette
    print("starlette %s" % getattr(starlette, "__version__", "?"))
except Exception as e:
    print("FAIL starlette ->", e)

print()
if bad:
    sys.exit(1)
print("ALL CORE IMPORTS OK")
