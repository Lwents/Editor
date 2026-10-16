from pathlib import Path


class RuntimeLease:
    """Prevent two backends from claiming the same persisted task queue."""
    def __init__(self, storage):
        self.path=Path(storage)/'backend.lock'
        self.stream=None

    def acquire(self):
        import os
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.stream=self.path.open('a+b')
        self.stream.write(b'0');self.stream.flush();self.stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.stream.close();self.stream=None
            raise RuntimeError('STORAGE_IN_USE: Backend khác đang dùng thư mục tác vụ này; không khởi động hàng đợi lần hai.') from None

    def release(self):
        if self.stream:
            self.stream.close();self.stream=None
