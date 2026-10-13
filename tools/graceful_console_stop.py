"""Send Ctrl+C only to the explicitly supplied isolated Windows server console."""
import ctypes
import sys
import os


def main():
    pid = int(sys.argv[1])
    allowed = {int(v) for v in sys.argv[2:]} | {pid, os.getpid()}
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.FreeConsole()
    if not kernel.AttachConsole(pid):
        raise RuntimeError('Cannot attach to server console; no signal sent')
    processes = (ctypes.c_uint32 * 64)()
    count = kernel.GetConsoleProcessList(processes, len(processes))
    actual = set(processes[:count])
    if not actual or count > len(processes) or not actual <= allowed:
        kernel.FreeConsole()
        raise RuntimeError(f'Console processes {sorted(actual)} are not isolated; no signal sent')
    kernel.SetConsoleCtrlHandler(None, True)
    if not kernel.GenerateConsoleCtrlEvent(0, 0):
        raise RuntimeError('Cannot send Ctrl+C')
    kernel.FreeConsole()
    print('Ctrl+C sent to verified server console')


if __name__ == '__main__':
    main()
