"""Additional independent semantic, DSP and real-process publication checks.

Run sequentially after the performance job, outside all timing samples.
"""
import contextlib,json,math,os,pathlib,shutil,signal,subprocess,time,traceback,wave
import numpy as np
import psutil
import common as c
import mac_process
p=c.arguments(__doc__);a=p.parse_args();c.setup(a.work);w=c.WORK;root=w/'supplemental';root.mkdir(exist_ok=True);log=w/'correctness.jsonl';f=w/'fixtures'
def require(ok,message):
 if not ok:raise AssertionError(message)
def record(id,name,fn,category='required'):
 try:e=fn();status='PASS'
 except Exception as ex:e=dict(error=str(ex),trace=traceback.format_exc()[-2000:]);status='FAIL'
 c.append(log,dict(entry=id,gate=name,category=category,status=status,evidence=e));print(id,status,name,str(e)[:250] if status=='FAIL' else '',flush=True)
def tone(path,hz,sr=48000):
 t=np.arange(sr)/sr;x=np.repeat((.2*np.sin(2*np.pi*hz*t))[:,None],2,axis=1)
 with wave.open(str(path),'wb') as out:out.setparams((2,2,sr,0,'NONE','not compressed'));out.writeframes(np.round(x*32767).astype('<i2').tobytes())
def central_rms(x,sr):
 x=x[round(.1*sr):-round(.1*sr)]
 require(len(x)>0,'empty analysis window');return float(np.sqrt(np.mean(x.astype(float)**2)))
def profile_ref(src,out,ratio,rate):
 cmd=[c.FF,'-v','error','-i',src,'-filter_complex',c.graph(1,ratio,rate),'-map','[out]','-map_metadata','-1','-map_chapters','-1','-c:a','pcm_f32le' if rate==44100 else 'pcm_s24le','-f','wav',out]
 c.tool(cmd)
# Each help command starts a new process. File caches are not purged; this is
# startup through canonical help, not a cold-disk export or loaded DSP benchmark.
startup=[]
for repetition in range(-1,7):
 shift=(repetition+1)%len(c.IDS);order=c.IDS[shift:]+c.IDS[:shift]
 for id in order:
  start=time.perf_counter();result=c.run(id,['--help']);seconds=time.perf_counter()-start
  require(result.returncode==0 and result.stdout.strip(),'canonical startup help failed')
  startup.append(dict(entry=id,repetition=repetition,warmup=repetition==-1,wall_seconds=seconds,cold_process=True,file_caches_purged=False,node_adapter_help_only=id=='node'))
(w/'startup.json').write_text(json.dumps(startup,indent=2))
# A deterministic, valid raw two-frame MP3 exposes an automatic input-probe
# limitation separately from the declared-format output verification correction.
raw_mp3=root/'raw-two-frame.mp3'
if not raw_mp3.exists():
 generated=c.run('node',[f/'short.wav','-o',raw_mp3]);require(generated.returncode==0,generated.stderr.decode(errors='replace'))
raw_audio,raw_rate,raw_meta=c.decode(raw_mp3)
require(len(raw_audio)>0 and np.isfinite(raw_audio).all(),'raw MP3 source is not valid decoded audio')
require(raw_meta['streams'][0]['codec_name']=='mp3','raw fixture is not MPEG Layer III')
raw_evidence=dict(bytes=raw_mp3.stat().st_size,sha256=c.sha(raw_mp3),declared_probe=raw_meta,decoded_frames=len(raw_audio),sample_rate=raw_rate,automatic_probe=c.probe(raw_mp3,declared=False))
try:
 ref=root/'raw-two-frame-reference.wav';c.reference([raw_mp3],ref)
 raw_evidence['frozen_reference']=dict(status='PASS',decoded_frames=len(c.decode(ref)[0]))
except RuntimeError as error:
 raw_evidence['frozen_reference']=dict(status='FAIL',error=str(error))
(w/'raw-two-frame-input-reference.json').write_text(json.dumps(raw_evidence,indent=2))
for id in c.IDS:
 d=root/id
 if d.exists():shutil.rmtree(d) # generated task-owned outputs, as in correctness.py
 d.mkdir()
 for label,value in [('empty',''),('leading-space',' 100'),('trailing-space','100 '),('hex-float','0x1p7'),('underscore','1_00'),('overflow','1e999'),('nan-uppercase','NaN'),('infinity-word','Infinity')]:
  def invalid_decimal(label=label,value=value):
   out=d/('invalid-decimal-'+label+'.wav');r=c.run(id,[f/'second.wav','-o',out,'--speed',value])
   require(r.returncode!=0,f'accepted non-decimal/nonfinite rate {value!r}');require(bool(r.stderr.strip()),'no invalid-rate diagnostic');require(not out.exists(),'invalid rate published output')
   return dict(value=value,exit=r.returncode,stderr=r.stderr.decode(errors='replace')[-300:])
  record(id,'cli-finite-decimal-'+label,invalid_decimal)
 def raw_mp3_input():
  out=d/'raw-mp3-input.wav';r=c.run(id,[raw_mp3,'-o',out])
  require(r.returncode==0,'Valid two-frame MP3 input is unsupported by automatic detection: '+r.stderr.decode(errors='replace')[-600:])
  x,sr,_=c.decode(out);require(len(x)>0 and np.isfinite(x).all(),'raw MP3 input export empty/nonfinite')
  return dict(input_frames=len(raw_audio),input_rate=raw_rate,output_frames=len(x),output_rate=sr)
 record(id,'valid-two-frame-mp3-input',raw_mp3_input)
 for mixed in [False,True]:
  def late_corruption(mixed=mixed):
   work=d/('late-corruption-mixed' if mixed else 'late-corruption-single');work.mkdir(exist_ok=True);src=work/'late.wav';src.write_bytes((f/'second.wav').read_bytes()[:-1]);out=work/'out.wav';out.write_bytes(b'preserve late corruption')
   tracks=[f/'second.wav',src] if mixed else [src]
   r=c.run(id,[*tracks,'-o',out,'--overwrite']);require(r.returncode!=0,'late damaged PCM packet accepted after a decodable prefix')
   require(out.read_bytes()==b'preserve late corruption','late corruption replaced old destination')
   extras=[q.name for q in work.iterdir() if q not in [src,out]];require(not extras,f'late corruption left staging {extras}')
   return dict(exit=r.returncode,stderr=r.stderr.decode(errors='replace')[-400:])
  record(id,'late-corrupt-pcm-'+('mixed' if mixed else 'single'),late_corruption)
 def absent_failure():
  work=d/'absent-failure';work.mkdir(exist_ok=True);src=work/'corrupt.wav';src.write_bytes(b'not a decodable audio container');out=work/'out.wav'
  r=c.run(id,[src,'-o',out]);require(r.returncode!=0,'corrupt source accepted');require(not out.exists(),'failed render published final output')
  extras=[q.name for q in work.iterdir() if q!=src];require(not extras,f'failed render left staging {extras}')
  return dict(exit=r.returncode,stderr=r.stderr.decode(errors='replace')[-300:])
 record(id,'failed-render-with-absent-destination',absent_failure)
 def absent_boundary():
  work=d/'absent-boundary';work.mkdir(exist_ok=True);src=f/'boundary-48000-64.wav';out=work/'out.wav';ref=work/'reference.wav';c.reference([src],ref,4)
  x,_,_=c.decode(ref);require(len(x)==0,'boundary reference unexpectedly nonempty')
  r=c.run(id,[src,'-o',out,'--speed','400'])
  if r.returncode==0:
   y,sr,_=c.decode(out);require(len(y)>0 and np.isfinite(y).all(),'reference-empty boundary claimed a header-only/nonfinite success')
   require(abs(len(y)/sr-64/48000/4)<=.005,'boundary extension duration')
   return dict(reference_frames=0,candidate_frames=len(y),extension_beyond_reference=True,excluded_from_speed=True)
  require(not out.exists(),'reference-empty failure published final output')
  extras=[q.name for q in work.iterdir() if q!=ref];require(not extras,f'reference-empty failure left staging {extras}')
  return dict(exit=r.returncode,reference_frames=0,excluded_from_speed=True)
 record(id,'reference-empty-absent-destination',absent_boundary,'boundary')
 def continuity():
  out=d/'continuity.wav';out.unlink(missing_ok=True);r=c.run(id,[f/'mono-44100.wav',f/'stereo-32000.wav','-o',out,'--speed','125.5']);require(r.returncode==0,r.stderr.decode())
  x,sr,_=c.decode(out);center=round(sr/1.255)
  # A 2 ms RMS window averages zero crossings. There must be no unexplained
  # 5 ms silent gap or tail on sustained nonzero input tones.
  width=round(.002*sr);energy=np.array([np.sqrt(np.mean(x[i:i+width].astype(float)**2)) for i in range(max(0,center-round(.02*sr)),min(len(x)-width,center+round(.02*sr)),width)])
  tail=float(np.sqrt(np.mean(x[-round(.005*sr):].astype(float)**2)))
  require(float(energy.min())>.02 and tail>.02,f'join RMS {energy.tolist()} tail RMS {tail}')
  return dict(join_min_2ms_rms=float(energy.min()),tail_5ms_rms=tail)
 record(id,'join-continuity-and-no-silent-tail',continuity)
 def cancellation():
  dest=d/'cancel.wav';dest.write_bytes(b'preserve actual render');cwd,cmd=c.ENTRIES[id]
  proc=subprocess.Popen([*cmd,str(f/'ten-minute.wav'),'-o',str(dest),'--speed','25','--overwrite'],cwd=cwd,env=c.ENV,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  seen=set();children=[];start=time.monotonic()
  try:
   deadline=start+15;ready=False
   while time.monotonic()<deadline and proc.poll() is None:
    tree=mac_process.snapshot(proc.pid)
    seen.update(q['pid'] for q in tree if q['name']=='ffmpeg')
    # C has one validator followed by its final FFmpeg; other entries have a
    # single FFmpeg process (Node cancellation targets the active decoder).
    if any(q['name']=='ffmpeg' for q in tree) and (id!='c' or len(seen)>=2):ready=True;break
    time.sleep(.002)
   require(ready,'did not observe actual active audio child')
   children=psutil.Process(proc.pid).children(recursive=True);proc.send_signal(signal.SIGTERM)
   stdout,stderr=proc.communicate(timeout=10)
   require(proc.returncode!=0,'signal incorrectly succeeded');require(dest.read_bytes()==b'preserve actual render','old destination changed')
   live=[q.pid for q in children if q.is_running() and q.status()!=psutil.STATUS_ZOMBIE];require(not live,f'live children after cancellation {live}')
   extras=[q.name for q in d.iterdir() if 'cancel.wav' in q.name and q!=dest];require(not extras,f'left temporary {extras}')
   return dict(exit=proc.returncode,observed_ffmpeg_pids=sorted(seen),seconds=time.monotonic()-start,stderr=stderr.decode(errors='replace')[-400:])
  finally:
   if proc.poll() is None:proc.kill();proc.wait()
   for q in children:
    with contextlib.suppress(psutil.NoSuchProcess):
     if q.is_running() and q.status()!=psutil.STATUS_ZOMBIE:q.kill()
 record(id,'actual-active-audio-cancellation',cancellation)
 def publication():
  dest=d/'publication.wav';dest.unlink(missing_ok=True);cwd,cmd=c.ENTRIES[id]
  proc=subprocess.Popen([*cmd,str(f/'ten-minute.wav'),'-o',str(dest),'--speed','25'],cwd=cwd,env=c.ENV,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  observations=[];start=time.monotonic()
  try:
   while proc.poll() is None and time.monotonic()-start<180:
    try:observations.append(dest.stat().st_size)
    except FileNotFoundError:pass
    time.sleep(.001)
   stdout,stderr=proc.communicate(timeout=1);require(proc.returncode==0,stderr.decode(errors='replace'))
   size=dest.stat().st_size;require(size>44,'empty output');require(not any(n!=size for n in observations),f'partial final destination visible: observed min={min(observations,default=None)} final={size}')
   return dict(final_bytes=size,visible_observations=len(observations),observed_sizes=sorted(set(observations)))
  finally:
   if proc.poll() is None:
    for child in psutil.Process(proc.pid).children(recursive=True):
     with contextlib.suppress(psutil.NoSuchProcess):child.kill()
    proc.kill();proc.wait()
   dest.unlink(missing_ok=True)
 record(id,'atomic-fresh-final-visibility',publication)
 # Exercise the remaining generated sample-rate fixtures as well as the
 # original 44.1 kHz waveform gates, without changing any reference threshold.
 for sample_rate in [32000,48000]:
  for kind in ['silence','noise','impulse','sweep']:
   for ratio in [.25,.5,.735,1,1.255,2,4]:
    name=f'waveform-{kind}-{sample_rate}-r{ratio}'
    def waveform(sample_rate=sample_rate,kind=kind,ratio=ratio,name=name):
     src=f/f'{kind}-{sample_rate}.wav';out=d/(name+'.wav');ref=d/(name+'-ref.wav')
     r=c.run(id,[src,'-o',out,'--speed',ratio*100]);require(r.returncode==0,r.stderr.decode(errors='replace'))
     if id=='node':
      cwd,cmd=c.ENTRIES[id]
      ref_run=subprocess.run([cmd[0],'--import',str(c.WORK/'node/node_modules/tsx/dist/loader.mjs'),str(c.ROOT/'competition/harness/node-reference.mjs'),str(ref),str(ratio*100),str(src)],cwd=cwd,env=c.ENV,capture_output=True,timeout=180)
      require(ref_run.returncode==0,ref_run.stderr.decode(errors='replace'))
     else:c.reference([src],ref,ratio)
     x,sr,meta=c.decode(out);y,_,_=c.decode(ref);require(x.shape==y.shape,f'waveform frame/channel mismatch {x.shape} vs {y.shape}')
     require(len(x)>0 and np.isfinite(x).all(),'empty/nonfinite waveform')
     expected=1/ratio;require(abs(len(x)/sr-expected)<=max(.005,.001*expected),'waveform duration')
     delta=x.astype(float)-y;rms=float(np.sqrt(np.mean(delta*delta)));peak=float(np.max(np.abs(delta)))
     require(rms<=1e-4 and peak<=1e-3,f'waveform RMS={rms} peak={peak}')
     return dict(source_rate=sample_rate,output_rate=sr,frames=len(x),rms_error=rms,peak_error=peak)
    record(id,name,waveform)
 # Native exact waveform parity already proves matching resampler response.
 # Explicit independent passband/alias measurements include Node's alternative
 # shared sinc DSP against its 44.1 kHz FFmpeg profile reference.
 for ratio in [.5,1.255,2,4]:
  for band,fractions in [('passband',[.1,.4,.7,.79]),('alias',[1.15,1.5])]:
   if band=='alias' and ratio<=1:continue
   for fraction in fractions:
    rate=44100 if id=='node' else 48000;surviving=min(24000,rate/(2*ratio));hz=surviving*fraction
    if hz>=23900:continue
    name=f'spectral-{band}-r{ratio}-f{fraction}'
    def spectral(ratio=ratio,rate=rate,hz=hz,band=band,name=name):
     src=d/(name+'-input.wav');out=d/(name+'.wav');ref=d/(name+'-ref.wav');tone(src,hz)
     r=c.run(id,[src,'-o',out,'--speed',ratio*100]);require(r.returncode==0,r.stderr.decode(errors='replace'))
     profile_ref(src,ref,ratio,rate);x,sr,_=c.decode(out);y,_,_=c.decode(ref)
     # Same central windows, full-band RMS for a single out-of-band sinusoid,
     # with the contract's 1e-7 FS floor. No window or threshold is retuned.
     xr=central_rms(x,sr);yr=central_rms(y,sr);delta=20*math.log10(max(xr,1e-7)/max(yr,1e-7))
     require(delta>=-1 if band=='passband' else delta<=3,f'{band} response delta {delta:.3f} dB (candidate={xr}, reference={yr})')
     return dict(input_hz=hz,candidate_rms=xr,reference_rms=yr,delta_db=delta,floor=1e-7,reference_sample_rate=rate)
    record(id,name,spectral,'dsp')
 # Reproduce the exact short MP3 performance exclusions with full probe evidence.
 if id=='node':
  def short_mp3():
   out=d/'short-unity.mp3';r=c.run(id,[f/'short.wav','-o',out]);require(r.returncode==0,r.stderr.decode(errors='replace'));info=c.probe(out)
   (w/'node-short-mp3-probe.json').write_text(json.dumps(info,indent=2));s=next(q for q in info['streams'] if q['codec_type']=='audio')
   require(s['codec_name']=='mp3',f'Expected MP3, FFprobe identified {s["codec_name"]}; full evidence saved')
   return info
  record(id,'short-20ms-unity-mp3-codec',short_mp3)
print('SUPPLEMENTAL COMPLETE',flush=True)
