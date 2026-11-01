import urllib.request, hashlib, zipfile, os, sys

proxy = urllib.request.ProxyHandler({'https': 'http://127.0.0.1:7897', 'http': 'http://127.0.0.1:7897'})
urllib.request.install_opener(urllib.request.build_opener(proxy))

base = 'https://github.com/HarrierOnChain/Prediction-Markets-Trading-Bot-Toolkits/releases/download/v0.1.0/'
zip_name = 'polymarket-toolkits-v0.1.0-x86_64-pc-windows-msvc.zip'
sha_name = zip_name + '.sha256'

outdir = '_pmt'
os.makedirs(outdir, exist_ok=True)

print('下载', zip_name)
urllib.request.urlretrieve(base + zip_name, os.path.join(outdir, zip_name))
print('下载', sha_name)
urllib.request.urlretrieve(base + sha_name, os.path.join(outdir, sha_name))

with open(os.path.join(outdir, zip_name), 'rb') as f:
    actual = hashlib.sha256(f.read()).hexdigest()
sha_text = open(os.path.join(outdir, sha_name)).read().strip()
expected = sha_text.split()[0]
print('实际 SHA256:', actual)
print('期望 SHA256:', expected)
if actual.lower() != expected.lower():
    print('SHA256 校验失败!')
    sys.exit(1)
print('SHA256 校验通过')

print('解压...')
with zipfile.ZipFile(os.path.join(outdir, zip_name)) as zf:
    zf.extractall(outdir)
    print('包内文件:', zf.namelist())
print('完成')
