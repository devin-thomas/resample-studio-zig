"""Fast Darwin child discovery and resident bytes from the host SDK libproc ABI.

proc_listchildpids avoids psutil's host-wide per-process parent scans. Task-info
CPU is Mach absolute time ticks (converted with mach_timebase_info); RUSAGE_CHILDREN remains the authoritative full-lifetime CPU.
"""
import ctypes as C
import ctypes.util
class Timebase(C.Structure):
 _fields_=[('numer',C.c_uint32),('denom',C.c_uint32)]
system=C.CDLL('/usr/lib/libSystem.B.dylib')
timebase=Timebase();system.mach_timebase_info(C.byref(timebase))
TIME_SCALE=timebase.numer/timebase.denom/1e9
lib=C.CDLL(ctypes.util.find_library('proc'),use_errno=True)
lib.proc_listchildpids.argtypes=[C.c_int,C.c_void_p,C.c_int]
lib.proc_listchildpids.restype=C.c_int
lib.proc_pidinfo.argtypes=[C.c_int,C.c_int,C.c_uint64,C.c_void_p,C.c_int]
lib.proc_pidinfo.restype=C.c_int
lib.proc_name.argtypes=[C.c_int,C.c_void_p,C.c_uint32]
lib.proc_name.restype=C.c_int
class TaskInfo(C.Structure):
 _fields_=[(s,C.c_uint64) for s in ['virtual','resident','user','system','threads_user','threads_system']]+[(s,C.c_int32) for s in ['policy','faults','pageins','cow','messages_sent','messages_received','mach_calls','unix_calls','switches','threads','running','priority']]
assert C.sizeof(TaskInfo)==96

def children(pid):
 buffer=(C.c_int*4096)()
 n=lib.proc_listchildpids(pid,buffer,C.sizeof(buffer))
 if n<0:return []
 if n>=len(buffer):raise RuntimeError('libproc child buffer exceeded')
 return [int(q) for q in buffer[:n] if q>0]

def snapshot(root):
 pids=[root];seen=set();rows=[]
 while pids:
  pid=pids.pop()
  if pid in seen:continue
  seen.add(pid);pids.extend(children(pid))
  task=TaskInfo();n=lib.proc_pidinfo(pid,4,0,C.byref(task),C.sizeof(task))
  if n!=C.sizeof(task):continue # process exited between enumeration and read
  name=C.create_string_buffer(128);lib.proc_name(pid,name,len(name))
  rows.append(dict(pid=pid,name=name.value.decode(errors='replace'),rss=int(task.resident),cpu_seconds=(task.user+task.system)*TIME_SCALE))
 return rows
