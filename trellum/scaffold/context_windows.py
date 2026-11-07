"""Windows scaffold access with pinned, non-reparse directory ancestors."""
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path


def _kernel():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    return kernel


@contextmanager
def _handle(path, *, directory=False):
    import ctypes

    kernel = _kernel()
    # No FILE_SHARE_DELETE: ancestors cannot be renamed/replaced while pinned.
    # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES engages sharing checks;
    # attribute-only directory handles do not prevent rename or deletion.
    # OPEN_REPARSE_POINT inspects the link itself rather than following it.
    handle = kernel.CreateFileW(str(path), 0x81 if directory else 0x80000000,
                                3 if directory else 1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = (ctypes.c_ulong * 2)()
        if not kernel.GetFileInformationByHandleEx(handle, 9, attributes, ctypes.sizeof(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
        if (attributes[0] & 0x400 or bool(attributes[0] & 0x10) != directory
                or kernel.GetFileType(handle) != 1):
            raise OSError('scaffold path is a reparse point or has the wrong type')
        yield handle
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def directory(root, name='', created=None):
    root = Path(os.path.abspath(root))
    with ExitStack() as stack:
        # Check and pin the project ancestors too, without resolving away links.
        for path in (*reversed(root.parents), root):
            stack.enter_context(_handle(path, directory=True))
        path = root
        walked = []
        for part in name.split('/') if name else ():
            walked.append(part)
            path = path / part
            if created is not None:
                try:
                    path.mkdir()
                except FileExistsError:
                    pass
                else:
                    created.append('/'.join(walked))
            stack.enter_context(_handle(path, directory=True))
        yield path


def read_file(parent, name):
    import msvcrt

    with _handle(parent / name) as handle:
        fd = msvcrt.open_osfhandle(_duplicate(handle), os.O_RDONLY | os.O_BINARY)
        with os.fdopen(fd, 'rb') as stream:
            data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError('file too large to verify')
            return data


def _duplicate(handle):
    import ctypes
    from ctypes import wintypes

    kernel = _kernel()
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                      ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                      wintypes.BOOL, wintypes.DWORD]
    copy = wintypes.HANDLE()
    process = kernel.GetCurrentProcess()
    if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(copy), 0, False, 2):
        raise ctypes.WinError(ctypes.get_last_error())
    return copy.value


def check(path, *, directory=False):
    with _handle(path, directory=directory):
        pass
