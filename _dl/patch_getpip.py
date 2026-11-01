import os

src = open('get-pip.py', encoding='utf-8').read()
old = 'tmpdir = tempfile.mkdtemp()'
tmp = os.path.join(os.getcwd(), 'gptmp').replace(chr(92), '/')
new = 'tmpdir = ' + repr(tmp)
assert old in src, 'pattern not found'
open('get-pip-fixed.py', 'w', encoding='utf-8').write(src.replace(old, new))
print('patched ->', new)
