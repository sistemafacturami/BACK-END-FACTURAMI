import contextlib
import errno
import json
import os
import shutil
import threading

try:
    import fcntl            # Linux (produccion)
except ImportError:         # Windows (modo portable): sin bloqueo entre procesos
    fcntl = None

_thread_lock = threading.Lock()


@contextlib.contextmanager
def _file_lock(path):
    if fcntl is None:
        yield
        return
    fd = os.open(path + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def save_users(users, path):
    payload = json.dumps(users, ensure_ascii=False, indent=2).encode('utf-8')
    with _thread_lock, _file_lock(path):
        tmp = '%s.tmp.%d.%d' % (path, os.getpid(), threading.get_ident())
        try:
            with open(tmp, 'wb') as f:
                f.write(payload)
                f.flush()
                with contextlib.suppress(OSError):
                    os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError as e:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            if e.errno not in (errno.EBUSY, errno.EXDEV):
                raise
            # montaje de archivo suelto: escribir en sitio, con copia previa
            if os.path.exists(path):
                shutil.copy2(path, path + '.bak')
            with open(path, 'r+b') as f:
                f.write(payload)
                f.truncate()
                f.flush()
                with contextlib.suppress(OSError):
                    os.fsync(f.fileno())
