"""Native frame operations with an exact Python reference and bounded cache."""
from collections import OrderedDict
import ctypes
from functools import lru_cache
import os
from pathlib import Path
import sys
import time


@lru_cache(maxsize=1)
def native_library():
    if os.environ.get('EDITOR_NATIVE_FRAMES', '1') == '0':
        return None
    suffix = '.dll' if sys.platform == 'win32' else '.so'
    path = Path(__file__).resolve().parents[2] / 'native' / ('frame_kernels' + suffix)
    try:
        lib = ctypes.CDLL(str(path))
        ptr = ctypes.c_void_p
        size = ctypes.c_size_t
        lib.subtitle_masks.argtypes = [ptr, ptr, size, size, ptr, ptr, ptr]
        lib.subtitle_masks.restype = ctypes.c_int
        lib.subtitle_compare.argtypes = [ptr, ptr, size, size, size, size,
                                        ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64)]
        lib.subtitle_compare.restype = None
        return lib
    except OSError:
        return None


def caption_masks(stack, gray):
    import cv2
    import numpy as np
    lib = native_library()
    if lib is not None:
        stack, gray = np.ascontiguousarray(stack), np.ascontiguousarray(gray)
        white, signature, scratch = (np.empty(gray.shape, dtype=np.uint8) for _ in range(3))
        result = lib.subtitle_masks(stack.ctypes.data, gray.ctypes.data, *gray.shape,
                                   white.ctypes.data, signature.ctypes.data, scratch.ctypes.data)
        if result != 0:
            raise RuntimeError('Native subtitle mask operation failed')
        return white, signature
    spread = stack.max(axis=2).astype(np.int16) - stack.min(axis=2)
    white = ((gray >= 190) & (spread <= 65)).astype(np.uint8)
    dark = cv2.dilate((gray < 135).astype(np.uint8), np.ones((5, 5), np.uint8))
    signature = white & dark
    if np.count_nonzero(signature) < 30:
        signature = white
    return white, signature


def compare_masks(current, previous):
    import numpy as np
    lib = native_library()
    if (lib is not None and current.shape == previous.shape and
            current.strides[0] >= 0 and previous.strides[0] >= 0 and
            current.strides[1] == previous.strides[1] == 1):
        changed, union = ctypes.c_uint64(), ctypes.c_uint64()
        lib.subtitle_compare(current.ctypes.data, previous.ctypes.data, *current.shape,
                             current.strides[0], previous.strides[0],
                             ctypes.byref(changed), ctypes.byref(union))
        return changed.value, union.value
    return int(np.count_nonzero(current != previous)), int(np.count_nonzero(current | previous))


class FrameCache:
    """Cache only recent decoded frames; never retain a whole film in RAM."""
    def __init__(self, capture, budget=32 * 1024 * 1024):
        self.capture = capture
        self.budget = budget
        self.frames = OrderedDict()
        self.bytes = 0
        self.hits = self.reads = self.seeks = 0
        self.decode_seconds = 0.0

    def remember(self, index, frame):
        if frame.nbytes > self.budget:
            return
        old = self.frames.pop(index, None)
        if old is not None:
            self.bytes -= old.nbytes
        while self.frames and self.bytes + frame.nbytes > self.budget:
            _, old = self.frames.popitem(last=False)
            self.bytes -= old.nbytes
        self.frames[index] = frame
        self.bytes += frame.nbytes

    def read_at(self, index):
        import cv2
        if index in self.frames:
            self.hits += 1
            frame = self.frames.pop(index)
            self.frames[index] = frame
            return True, frame
        started = time.perf_counter()
        if round(self.capture.get(cv2.CAP_PROP_POS_FRAMES)) != index:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            self.seeks += 1
        ok, frame = self.capture.read()
        self.decode_seconds += time.perf_counter() - started
        self.reads += 1
        if ok:
            self.remember(index, frame)
        return ok, frame
