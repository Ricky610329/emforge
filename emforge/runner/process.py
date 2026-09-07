"""只持有並終止自己的行程樹；Windows Job Object 在執行端死亡時回收子樹。"""
import ctypes
import os
import signal
import subprocess


def _windows_job(process):
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [("user", ctypes.c_longlong), ("job", ctypes.c_longlong), ("flags", w.DWORD),
                    ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t), ("active", w.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("read", "write", "other", "rbytes", "wbytes", "obytes")]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_mem", ctypes.c_size_t), ("job_mem", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = w.HANDLE
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
    k.CloseHandle.argtypes = [w.HANDLE]
    handle = k.CreateJobObjectW(None, None)
    info = Extended()
    info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle or not k.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        if handle:
            k.CloseHandle(handle)
        raise OSError(ctypes.get_last_error(), "無法建立算法 Job Object")
    if not k.AssignProcessToJobObject(handle, w.HANDLE(int(process._handle))):
        k.CloseHandle(handle)
        raise OSError(ctypes.get_last_error(), "無法隔離算法行程")
    return k, handle


class OwnedProcess:
    def __init__(self, python, entrypoint, cwd, env, stdout):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.job = None
        self.proc = subprocess.Popen([python, "-u", "-m", "emforge.runner.execute", str(entrypoint)],
                                     cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=stdout,
                                     stderr=subprocess.STDOUT, creationflags=flags,
                                     start_new_session=os.name != "nt")
        try:
            if os.name == "nt":
                self.job = _windows_job(self.proc)
            self.proc.stdin.write(b"go\n")
            self.proc.stdin.flush()
        except BaseException:
            self.proc.kill()
            self.proc.wait(timeout=5)
            self.close()
            raise

    def terminate(self):
        if self.job:
            k, handle = self.job
            k.TerminateJobObject(handle, 1)
        elif self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc.wait(timeout=5)

    def close(self):
        if self.job:
            k, handle = self.job
            k.CloseHandle(handle)
            self.job = None
        if self.proc.stdin:
            self.proc.stdin.close()

    @property
    def pid(self):
        return self.proc.pid

    def poll(self):
        return self.proc.poll()

def process_identity(pid):
    """辨識 PID 的建立時間，避免殘留鎖誤認重用的 PID。"""
    if os.name != "nt":
        from pathlib import Path
        try:
            return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[19]
        except FileNotFoundError:
            return None
    from ctypes import wintypes as w
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes, k.OpenProcess.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    k.GetProcessTimes.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return None
        raise OSError(ctypes.get_last_error(), "無法確認既有算法節點行程")
    stamps = [w.FILETIME() for _ in range(4)]
    try:
        if not k.GetProcessTimes(handle, *(ctypes.byref(x) for x in stamps)):
            raise OSError(ctypes.get_last_error(), "無法讀取行程建立時間")
        return str((stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime)
    finally:
        k.CloseHandle(handle)
