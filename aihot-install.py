import os, shutil, stat

ws = r"D:\Users\Administrator\Documents\deepseek-harness\default-workspace"
staging = os.path.join(ws, "aihot-staging")
target = r"C:\Users\Administrator\.dsh\skills\aihot"

# Runtime package files (byte-exact, already SHA-256 verified).
files = ["SKILL.md", "LICENSE", "agents/openai.yaml"]

os.makedirs(target, exist_ok=True)
for rel in files:
    src = os.path.join(staging, rel.replace("/", os.sep))
    dst = os.path.join(target, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o644)
    print("installed", rel)

# Pseudonymous Actor ID (--actor de4ff0a4-c0df-4e24-a32a-19ebff396c84), perms 0600.
actor = r"de4ff0a4-c0df-4e24-a32a-19ebff396c84" + "\n"
actor_path = os.path.join(target, ".aihot-actor-id")
with open(actor_path, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(actor)
try:
    os.chmod(actor_path, 0o600)
except Exception as e:
    print("chmod actor warning:", e)
print("installed .aihot-actor-id")

# Local .gitignore (matches installer: only the two dotfiles).
gitignore = "/.aihot-actor-id\n/.aihot-actor-disabled\n"
gi_path = os.path.join(target, ".gitignore")
with open(gi_path, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(gitignore)
print("installed .gitignore")

print("\n=== final tree ===")
for root, dirs, fnames in os.walk(target):
    for fn in sorted(fnames):
        p = os.path.join(root, fn)
        print(os.path.relpath(p, target), "(%d bytes)" % os.path.getsize(p))
