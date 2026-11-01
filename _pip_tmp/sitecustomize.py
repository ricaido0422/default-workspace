"""Workaround for the DSH Windows sandbox.

`tempfile.mkdtemp` applies chmod(0o700) to the new directory and the sandbox's
path-ACL checker then refuses writes into it (and refuses further chmod calls).
Replace it with a mkdir-based version that skips the restrictive chmod so the
directory inherits the (writable) ACL of its parent.
"""

import errno
import os
import shutil
import tempfile

_orig_mkdtemp = tempfile.mkdtemp
_seq = 0


def _patched_mkdtemp(suffix=None, prefix=None, dir=None, **kwargs):
    global _seq
    if dir is None:
        dir = tempfile.gettempdir()
    _seq += 1
    name = f"{prefix or 'tmp'}{os.getpid()}_{_seq}{suffix or ''}"
    path = os.path.join(dir, name)
    try:
        os.mkdir(path, 0o777)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise FileExistsError(f"{path} 已存在") from exc
        raise
    return path


def _patched_TemporaryDirectory(*args, **kwargs):
    class _TD(tempfile.TemporaryDirectory):
        def __init__(self, *a, **kw):
            kw.pop("ignore_cleanup_errors", None)
            super().__init__(*a, **kw)

        def _rmtree(self, name, ignore_errors=False, repeated=False):
            try:
                shutil.rmtree(name, ignore_errors=True)
            except OSError:
                pass

    return _TD(*args, **kwargs)


tempfile.mkdtemp = _patched_mkdtemp
tempfile.TemporaryDirectory = _patched_TemporaryDirectory
