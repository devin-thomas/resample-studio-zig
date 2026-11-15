"""Sequential end-to-end benchmark with rotated order, resumable raw records.

Speed passes have no process sampler. Memory/CPU instrumentation is separate.
All setup and output verification is outside the measured process lifetime.
"""
import contextlib,itertools,json,os,pathlib,random,resource,statistics,subprocess,threading,time
import numpy as np
import psutil
import common as c
p=c.arguments(__doc__);p.add_argument('--only',choices=['speed','memory','all'],default='all');p.add_argument('--repetitions',type=int,default=7);p.add_argument('--memory-repetitions',type=int,default=3);p.add_argument('--case-limit',type=int);p.add_argument('--entry',action='append',choices=c.IDS);a=p.parse_args()
if a.repetitions<7:raise SystemExit('Contract requires at least seven speed repetitions')
c.setup(a.work);w=c.WORK;f=w/'fixtures';outputs=w/'benchmark-outputs';outputs.mkdir(exist_ok=True)
raw=w/'runs.jsonl';preflight=w/'performance-gates.jsonl';entries=a.entry or c.IDS
workloads=[('short','short',1,.020),('second','second',1,1),('minute','minute',1,60),('ten-minute','ten-minute',1,600),('concat-2','half-minute',2,60),('concat-16','sixteenth',16,60)]
cases=[]
for (load,fixture,n,seconds),infmt,outfmt,ratio,publication in itertools.product(workloads,['wav','mp3'],['wav','mp3'],[.5,1,1.255,2],['fresh','overwrite']):
 cases.append(dict(id=f'{load}_{infmt}-{outfmt}_r{ratio}_{publication}',workload=load,fixture=fixture,inputs=n,input_format=infmt,output_format=outfmt,ratio=ratio,publication=publication,input_seconds=seconds))
if a.case_limit:cases=cases[:a.case_limit]
(w/'cases.json').write_text(json.dumps(cases,indent=2));done={(r['case'],r['entry'],r['pass'],r['repetition']) for r in c.readlines(raw)}
gates={(r['case'],r['entry']):r for r in c.readlines(preflight)}

def run_process(id,args,sample=False):
 cwd,cmd=c.ENTRIES[id];before=resource.getrusage(resource.RUSAGE_CHILDREN)
 samples=[];peaks={};observed={};stop=threading.Event();start=time.perf_counter()
 proc=subprocess.Popen([*cmd,*map(str,args)],cwd=cwd,env=c.ENV,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 def monitor():
  import mac_process
  previous=start
  while not stop.is_set():
   stamp=time.perf_counter();rss=0;detail=[]
   for q in mac_process.snapshot(proc.pid):
    identity=str(q['pid']);memory=q['rss'];rss+=memory
    peaks[identity]=max(memory,peaks.get(identity,0))
    observed[identity]=dict(pid=q['pid'],name=q['name'],max_sampled_rss=peaks[identity],cpu_seconds=q['cpu_seconds'])
    detail.append(dict(pid=q['pid'],rss=memory,name=q['name']))
   samples.append((stamp-start,rss,stamp-previous,detail));previous=stamp
   stop.wait(.002)
 monitor_thread=threading.Thread(target=monitor,daemon=True) if sample else None
 if sample:monitor_thread.start()
 try:stdout,stderr=proc.communicate(timeout=1200)
 except subprocess.TimeoutExpired:
  descendants=psutil.Process(proc.pid).children(recursive=True)
  for q in reversed(descendants):
   with contextlib.suppress(psutil.NoSuchProcess):q.kill()
  proc.kill();stdout,stderr=proc.communicate();stderr+=b'\nHarness timeout after 1200 seconds'
 end=time.perf_counter();stop.set()
 if sample:monitor_thread.join()
 after=resource.getrusage(resource.RUSAGE_CHILDREN)
 result=dict(returncode=proc.returncode,wall_seconds=end-start,cpu_user_seconds=after.ru_utime-before.ru_utime,cpu_system_seconds=after.ru_stime-before.ru_stime,stderr=stderr.decode(errors='replace')[-2000:])
 if sample:
  peak=max(samples,key=lambda q:q[1]) if samples else (0,0,0,[])
  intervals=[q[2] for q in samples[1:]]
  result.update(memory_backend='Darwin libproc proc_listchildpids / PROC_PIDTASKINFO',tree_peak_rss_bytes=peak[1],peak_at_seconds=peak[0],peak_processes=peak[3],samples=len(samples),sampling_interval_seconds_median=statistics.median(intervals) if intervals else None,sampling_interval_seconds_max=max(intervals) if intervals else None,observed_processes=list(observed.values()),max_single_process_sampled_rss_bytes=max(peaks.values(),default=0),sum_per_process_maxima_bytes=sum(peaks.values()),sampled_cpu_lower_bound_seconds=sum(q['cpu_seconds'] for q in observed.values()))
 return result

def output_valid(path,case,id,full=True):
 meta=c.probe(path);s=next(q for q in meta['streams'] if q['codec_type']=='audio')
 rate=44100 if id=='node' else 48000
 assert int(s['sample_rate'])==rate and s['channels']==2,f'wrong output profile {s}'
 assert s['codec_name']==('pcm_f32le' if id=='node' else 'pcm_s24le') if case['output_format']=='wav' else s['codec_name']=='mp3'
 # Stream through decoder: no whole-output memory allocation in harness.
 command=[c.FF,'-v','error','-i',str(path),'-map','0:a:0','-f','f32le','-c:a','pcm_f32le','-']
 nbytes=0;maxamp=0.;square=0.;nonfinite=False
 with subprocess.Popen(command,env=c.ENV,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as decoder:
  while True:
   data=decoder.stdout.read(1024*1024)
   if not data:break
   x=np.frombuffer(data,dtype='<f4');nonfinite |= not np.isfinite(x).all();nbytes+=len(data)
   if len(x):maxamp=max(maxamp,float(np.max(np.abs(x))));square+=float(np.sum(x.astype(float)**2))
  stderr=decoder.stderr.read();code=decoder.wait()
 assert code==0,stderr.decode(errors='replace')
 frames=nbytes//8;duration=frames/rate;expected=case['input_seconds']/case['ratio']
 tolerance=max(.005*case['inputs'],.001*expected)+(.05 if case['output_format']=='mp3' else 0)
 assert frames>0 and not nonfinite,'empty/nonfinite export'
 assert abs(duration-expected)<=tolerance,f'duration {duration} expected {expected} tolerance {tolerance}'
 return dict(frames=frames,duration_seconds=duration,finite=True,peak_amplitude=maxamp,rms=float(np.sqrt(square/(frames*2))),output_bytes=path.stat().st_size,sha256=c.sha(path),sample_rate=rate)

def prepare_out(out,publication):
 out.unlink(missing_ok=True)
 if publication=='overwrite':out.write_bytes(b'pre-existing destination\n')

def arguments_for(case,out):
 return [*[f/(case['fixture']+'.'+case['input_format'])]*case['inputs'],'-o',out,'--speed',case['ratio']*100,*(['--overwrite'] if case['publication']=='overwrite' else [])]

for index,case in enumerate(cases):
 print('CASE',index+1,'/',len(cases),case['id'],flush=True)
 c.append(w/'host-load.jsonl',dict(case=case['id'],unix_time=time.time(),load_average=os.getloadavg(),memory_available=psutil.virtual_memory().available,swap=psutil.swap_memory()._asdict(),cpu=psutil.cpu_times()._asdict()))
 # Establish reference nonemptiness, decoded duration and output profile before
 # allowing any export in the case to enter speed reporting.
 ref=outputs/('reference.'+case['output_format'])
 if (case['id'],'reference') not in gates:
  try:
   c.reference([f/(case['fixture']+'.'+case['input_format'])]*case['inputs'],ref,case['ratio'])
   evidence=output_valid(ref,case,'zig');row=dict(case=case['id'],entry='reference',status='PASS',evidence=evidence)
  except Exception as ex:row=dict(case=case['id'],entry='reference',status='FAIL',error=str(ex))
  c.append(preflight,row);gates[(case['id'],'reference')]=row;ref.unlink(missing_ok=True)
 if gates[(case['id'],'reference')]['status']!='PASS':
  print('EXCLUDED reference',case['id'],flush=True);continue
 # First process for each case is deliberately recorded separately as a
 # first-launch observation. Caches are never purged: this is NOT disk-cold.
 for id in entries:
  if (case['id'],id) in gates:continue
  out=outputs/(id+'.'+case['output_format']);prepare_out(out,case['publication'])
  r=run_process(id,arguments_for(case,out));row=dict(case=case['id'],entry=id,status='PASS',first_launch=r)
  try:
   assert r['returncode']==0,r['stderr'];row['evidence']=output_valid(out,case,id)
  except Exception as ex:row['status']='FAIL';row['error']=str(ex)
  c.append(preflight,row);gates[(case['id'],id)]=row
  print(' WARMUP',id,row['status'],round(r['wall_seconds'],4),flush=True)
  out.unlink(missing_ok=True)
 eligible=[id for id in entries if gates[(case['id'],id)]['status']=='PASS']
 passes=(['speed'] if a.only=='speed' else ['memory'] if a.only=='memory' else ['speed','memory'])
 for kind in passes:
  count=a.repetitions if kind=='speed' else a.memory_repetitions
  # Case-based starting order plus cyclic rotation distributes positions across
  # every repetition. No concurrent benchmark/render/build jobs.
  first=(index*5)%len(eligible) if eligible else 0
  for repetition in range(count):
   shift=(first+repetition)%len(eligible) if eligible else 0
   order=eligible[shift:]+eligible[:shift]
   for order_index,id in enumerate(order):
    key=(case['id'],id,kind,repetition)
    if key in done:continue
    out=outputs/(id+'.'+case['output_format']);prepare_out(out,case['publication'])
    r=run_process(id,arguments_for(case,out),sample=kind=='memory')
    r.update(case=case['id'],entry=id,**{'pass':kind},repetition=repetition,order_index=order_index,unix_time=time.time(),output_bytes=out.stat().st_size if out.exists() else 0)
    # Size and exit checks on every repetition; complete decoding occurred in
    # the untimed warmup. No failed/header-only output counts as a fast result.
    r['valid']=r['returncode']==0 and r['output_bytes']==gates[(case['id'],id)]['evidence']['output_bytes']
    c.append(raw,r);done.add(key);out.unlink(missing_ok=True)
    if not r['valid']:print(' RUN FAILED',id,kind,repetition,r,flush=True)
  print(' COMPLETE',kind,case['id'],flush=True)
print('BENCHMARK COMPLETE',flush=True)
