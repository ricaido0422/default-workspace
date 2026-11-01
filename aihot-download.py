import urllib.request, hashlib, os, ssl

base = "https://aihot.news/aihot-skill/"
files = ["SKILL.md", "LICENSE", "agents/openai.yaml", "manifest.sha256"]
staging = os.path.join(os.getcwd(), "aihot-staging")
os.makedirs(staging, exist_ok=True)

ctx = ssl.create_default_context()
ua = {"User-Agent": "aihot-skill-installer/1.0 (+https://aihot.news/aihot-skill/)"}

for f in files:
    url = base + f
    dest = os.path.join(staging, f.replace("/", os.sep))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers=ua)
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            data = r.read()
        with open(dest, "wb") as fh:
            fh.write(data)
        print("OK %s (%d bytes) %s" % (f, len(data), hashlib.sha256(data).hexdigest()))
    except Exception as e:
        print("FAIL %s: %s" % (f, e))

# verify
try:
    manifest = os.path.join(staging, "manifest.sha256")
    with open(manifest, "r", encoding="utf-8") as fh:
        lines = fh.read().strip().splitlines()
    ok = True
    for line in lines:
        parts = line.split()
        if len(parts) != 2:
            continue
        h, name = parts[0], parts[1]
        p = os.path.join(staging, name.replace("/", os.sep))
        with open(p, "rb") as fh:
            actual = hashlib.sha256(fh.read()).hexdigest()
        status = "MATCH" if actual == h else "MISMATCH"
        if actual != h:
            ok = False
        print("VERIFY %s %s (expected %s)" % (status, name, h))
    print("ALL_MATCH" if ok else "SOME_MISMATCH")
except Exception as e:
    print("VERIFY-FAIL: %s" % e)
