"""Windows 当前用户 DPAPI 加密凭证。没有可用 OS 加密服务时拒绝明文降级。"""
import ctypes
import json
import os
import tempfile
import threading
from pathlib import Path
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]


def crypt(data, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('当前凭证库需要 Windows DPAPI；禁止明文降级')
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    api = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not api(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError('无法使用当前 Windows 用户加解密凭证，请检查账户或备份')
    try:
        return ctypes.string_at(target.data, target.length)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


class CredentialVault:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return {}
        raw = self.path.read_bytes()
        if not raw.startswith(b'PM-DPAPI-1\n'):
            raise RuntimeError('凭证库格式无效；不会尝试明文读取')
        return json.loads(crypt(raw[11:], decrypt=True))

    def get(self, account_id):
        with self.lock:
            return dict(self._read().get(account_id, {}))

    def put(self, account_id, values):
        with self.lock:
            rows = self._read()
            rows[account_id] = dict(values)
            encrypted = b'PM-DPAPI-1\n' + crypt(json.dumps(rows).encode())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                    temporary = stream.name
                    stream.write(encrypted)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)

    def flags(self, account_id):
        return {key: bool(value) for key, value in self.get(account_id).items()}
