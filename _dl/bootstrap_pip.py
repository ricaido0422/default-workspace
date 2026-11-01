import os
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NORM = os.path.join(HERE, 'wheels')
os.makedirs(NORM, exist_ok=True)

WHEEL = os.path.join(HERE, 'pip-26.2.2.whl')
target = os.path.join(NORM, 'pip-26.2.2-py3-none-any.whl')
if not os.path.exists(target):
    with zipfile.ZipFile(WHEEL) as z, open(target, 'wb') as f:
        f.write(z.fp.read()) if False else None
        z.fp.seek(0)
        f.write(z.fp.read())
print('normalized wheel ->', target)

PY = r'D:\Users\Administrator\Documents\deepseek-harness\default-workspace\Python312\python.exe'
cmd = [PY, target + os.sep + 'pip', 'install', '--no-index', '--find-links', NORM, target]
print('running:', ' '.join(cmd))
sys.exit(subprocess.call(cmd))
