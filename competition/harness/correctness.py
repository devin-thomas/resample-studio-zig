"""Frozen contract gates. Record failures; never modify candidate/baseline sources."""
import base64,contextlib,json,math,os,pathlib,shutil,signal,struct,subprocess,sys,time,traceback,wave
import numpy as np
import psutil
import common as c
p=c.arguments(__doc__);p.add_argument('--entry',choices=c.IDS,action='append');a=p.parse_args();c.setup(a.work)
f=c.WORK/'fixtures';root=c.WORK/'correctness';root.mkdir(exist_ok=True);outlog=c.WORK/'correctness.jsonl'
entries=a.entry or c.IDS
def record(id,name,fun,category='required'):
 try: evidence=fun();status='PASS'
 except Exception as ex:evidence={'error':str(ex),'trace':traceback.format_exc()[-2500:]};status='FAIL'
 row=dict(entry=id,gate=name,category=category,status=status,evidence=evidence)
 c.append(outlog,row);print(id,status,name,str(evidence)[:180] if status=='FAIL' else '',flush=True)
def require(ok,message):
 if not ok:raise AssertionError(message)
def frequency(x,sr):
 x=x-x.mean();n=len(x);spec=np.abs(np.fft.rfft(x*np.hanning(n)));k=int(np.argmax(spec[1:])+1)
 correction=0
 if 0<k<len(spec)-1:
  v=np.log(np.maximum(spec[k-1:k+2],1e-20));correction=float(.5*(v[0]-v[2])/(v[0]-2*v[1]+v[2]))
 return (k+correction)*sr/n

def compare(x,y):
 # Native profile/reference must match within the specified absolute thresholds.
 # Codec delay is aligned via FFT cross-correlation, bounded to 4096 samples.
 lag=0
 if x.shape!=y.shape or not np.array_equal(x,y):
  n=min(len(x),len(y),96000);aa=x[:n,0].astype(float);bb=y[:n,0].astype(float)
  size=1<<(2*n-1).bit_length();corr=np.fft.irfft(np.fft.rfft(aa,size)*np.conj(np.fft.rfft(bb,size)),size)
  lim=min(4096,n-1);ls=np.arange(-lim,lim+1);lag=int(ls[np.argmax(corr[ls%size])])
 xx=x[max(0,lag):];yy=y[max(0,-lag):];n=min(len(xx),len(yy));require(n>0,'no aligned samples')
 d=xx[:n].astype(float)-yy[:n];rms=float(np.sqrt(np.mean(d*d)));peak=float(np.max(np.abs(d)))
 require(rms<=1e-4 and peak<=1e-3,f'waveform RMS={rms} peak={peak} lag={lag}')
 return dict(rms=rms,peak=peak,alignment_samples=lag,compared_frames=n)

def audio_case(id,d,name,inputs,ratio=1,flags=None,extension='wav',tone=False):
 out=d/(name+'.'+extension);ref=d/('ref-'+name+'.'+extension)
 result=c.run(id,[*inputs,'-o',out,*(['--speed',ratio*100] if flags is None else flags)],timeout=180)
 require(result.returncode==0,f'exit={result.returncode}: {result.stderr.decode(errors="replace")[-1000:]}')
 x,sr,meta=c.decode(out);require(len(x)>0 and np.isfinite(x).all(),'empty/nonfinite output')
 s=next(s for s in meta['streams'] if s['codec_type']=='audio');expected_sr=44100 if id=='node' else 48000
 require(sr==expected_sr and x.shape[1]==2,f'profile {s}')
 if extension=='wav':require(s['codec_name']==('pcm_f32le' if id=='node' else 'pcm_s24le'),f'codec {s["codec_name"]}')
 else:require(s['codec_name']=='mp3' and int(s.get('bit_rate',0))==320000,f'MP3 codec/bitrate {s}')
 durations=[len(c.decode(q)[0])/c.decode(q)[1] for q in inputs]
 expected=sum(durations)/ratio;tol=max(.005*len(inputs),.001*expected)+(.050 if extension=='mp3' else 0)
 require(abs(len(x)/sr-expected)<=tol,f'duration {len(x)/sr} expected {expected} tolerance {tol}')
 if id=='node':
  cwd,cmd=c.ENTRIES[id]
  r=subprocess.run([cmd[0],'--import','tsx',str(c.ROOT/'competition/harness/node-reference.mjs'),str(ref),str(ratio*100),*map(str,inputs)],cwd=cwd,env=c.ENV,capture_output=True,timeout=180)
  require(r.returncode==0,r.stderr.decode(errors='replace'))
 else:c.reference(inputs,ref,ratio)
 y,_,_=c.decode(ref);e=compare(x,y)
 if tone:
  start=0
  for i,duration in enumerate(durations):
   length=duration/ratio;lo=round((start+.15*length)*sr);hi=round((start+.85*length)*sr)
   for k in range(2):
    # First is mono 440 Hz; second stereo has L=880, R=1320 Hz.
    hz=(440 if i==0 else 880*(1 if k==0 else 1.5))*ratio
    measured=frequency(x[lo:hi,k],sr)
    require(abs(measured-hz)<=max(2,.005*hz),f'track {i} channel {k}: {measured}Hz expected {hz}')
   start+=length
 return dict(**e,frames=len(x),sample_rate=sr,duration_seconds=len(x)/sr,expected_seconds=expected,output_bytes=out.stat().st_size)

def safety(id,d):
 src=f/'second.wav';good=src.read_bytes();out=d/'safety.wav'
 def rejection(name,args,preserve=False,env=None):
  def test():
   out.unlink(missing_ok=True)
   if preserve:out.write_bytes(b'preserve destination')
   before=set(d.iterdir());r=c.run(id,args,env=env,cwd=d,timeout=15)
   require(r.returncode!=0,'accepted invalid input/options')
   require(bool(r.stderr.strip()),'missing error diagnostic')
   require(out.read_bytes()==b'preserve destination' if preserve else not out.exists(),'destination changed/published')
   extras=set(d.iterdir())-before-{out};require(not extras,f'left temporary files: {extras}')
   return dict(exit=r.returncode,stderr=r.stderr.decode(errors='replace')[-400:])
  record(id,name,test)
 for flag in [[],['--speed','nan'],['--speed','inf'],['--speed','25x'],['--speed','24.9'],['--speed','400.1'],['--cents','-2400.1'],['--cents','2400.1'],['--speed'],['--cents'],['--unknown'],['--speed','100','--speed','100'],['--cents','0','--cents','0'],['--speed','100','--cents','0'],['-o',str(out)],['--output',str(out)]]:
  if not flag:args=[src] # missing output
  else:args=[src,'-o',out,*flag]
  rejection('cli-invalid-'+('-'.join(flag) or 'missing-output'),args)
 rejection('cli-missing-input',['-o',out])
 rejection('cli-unsupported-output',[src,'-o',d/'bad.ogg'])
 for flags in [[],['-h'],['--help'],['--version']]:
  def t(flags=flags):
   r=c.run(id,flags);require(r.returncode==0 and bool(r.stdout.strip()),'help/version failed');return r.stdout.decode()[:160]
  record(id,'cli-info-'+(' '.join(flags) or 'no-arguments'),t)
 for name,content in [('empty-file.wav',b''),('corrupt.wav',b'not audio'),('zero-frames.wav',None),('partial-pcm.wav',None)]:
  q=d/name
  if content is None:
   with wave.open(str(q),'wb') as w:w.setparams((1,2,48000,0,'NONE','not compressed'));w.writeframes(b'\x01' if name=='partial-pcm.wav' else b'')
  else:q.write_bytes(content)
  for tracks in [[q],[src,q],[q,src]]:rejection('reject-'+name+'-'+str(len(tracks))+'-'+str(tracks.index(q)),[*tracks,'-o',out,'--overwrite'],True)
 # Reuse the reviewed, embedded Ogg fixture byte-for-byte without importing tests.
 import ast
 module=ast.parse((c.WORK/'candidates/implementations/go/validate.py').read_text());encoded=None
 for node in ast.walk(module):
  if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='b64decode' and node.args and isinstance(node.args[0],ast.Constant):encoded=node.args[0].value;break
 q=d/'packets-no-samples.ogg';q.write_bytes(base64.b64decode(encoded))
 def verify_empty():
  info=json.loads(c.tool([c.FP,'-v','error','-show_packets','-of','json',q]));require(len(info['packets'])>0,'no packets')
  require(len(c.decode(q)[0])==0,'not decoded-empty');return dict(packets=len(info['packets']),frames=0)
 record(id,'fixture-packets-with-zero-decoded-frames',verify_empty,'fixture')
 for tracks in [[q],[src,q],[q,src]]:rejection('reject-packet-only-'+str(len(tracks))+'-'+str(tracks.index(q)),[*tracks,'-o',out,'--overwrite'],True)
 rejection('missing-input',[d/'absent.wav','-o',out])
 rejection('directory-input',[d,'-o',out])
 fifo=d/'pipe.wav';os.mkfifo(fifo);rejection('fifo-input',[fifo,'-o',out])
 rejection('existing-no-overwrite',[src,'-o',out],True)
 rejection('missing-parent',[src,'-o',d/'absent'/'output.wav'])
 locked=d/'unwritable';locked.mkdir();locked.chmod(0o500)
 try:rejection('unwritable-parent',[src,'-o',locked/'out.wav'])
 finally:locked.chmod(0o700)
 # Missing tool PATH: absolute Node executable and absolute tsx loader retain
 # adapter startup, while FFmpeg/ffprobe are unavailable.
 missing=d/'no-tools';missing.mkdir()
 env=dict(c.ENV,PATH=str(missing))
 rejection('missing-dependencies',[src,'-o',out],env=env)
 for kind in ['direct','hardlink','symlink']:
  def alias(kind=kind):
   local=d/'alias-input.wav';shutil.copyfile(src,local);dest=d/'alias-output.wav'
   dest.unlink(missing_ok=True)
   if kind=='hardlink':os.link(local,dest)
   elif kind=='symlink':dest.symlink_to(local)
   else:dest=local
   r=c.run(id,[local,'-o',dest,'--overwrite'],cwd=d)
   require(r.returncode!=0,'accepted input alias');require(local.read_bytes()==good,'input modified');return dict(exit=r.returncode)
  record(id,'input-alias-'+kind,alias)
 def literals():
  local=d/"-space 'quote' $(literal); \u97f3.wav";shutil.copyfile(src,local)
  target=d/"literal output ' \u97f3.WAV";r=c.run(id,['-o',target.name,'--',local.name],cwd=d)
  require(r.returncode==0,r.stderr.decode(errors='replace'));require(len(c.decode(target)[0])>0,'empty literal export');require(local.read_bytes()==good,'literal input changed')
  require(not (d/'literal').exists(),'shell expansion');return dict(output=target.name)
 record(id,'literal-paths',literals)
 def overwrite():
  out.write_bytes(b'old');r=c.run(id,[src,'-o',out,'--overwrite']);require(r.returncode==0,r.stderr.decode());require(len(c.decode(out)[0])>0,'bad overwrite')
 record(id,'successful-overwrite',overwrite)

 def race(mode):
  work=d/('race-'+mode);work.mkdir();shim=work/'tools';shim.mkdir();ready=work/'ready';release=work/'release';pidfile=work/'child.pid'
  fake=shim/'ffmpeg'
  fake.write_text(f'#!{sys.executable}\nimport os,pathlib,time,sys\np=pathlib.Path({str(work)!r})\n(p/"child.pid").write_text(str(os.getpid()))\n(p/"ready").touch()\nwhile not (p/"release").exists():time.sleep(.01)\n'+('sys.stderr.write("injected child failure\\n");sys.exit(73)\n' if mode=='child-failure' else f'os.execv({c.FF!r},[{c.FF!r},*sys.argv[1:]])\n'))
  fake.chmod(0o755);(shim/'ffprobe').symlink_to(c.FP)
  target=work/'out.wav';source=work/'input.wav';shutil.copyfile(src,source)
  if mode in ['cancel-int','cancel-term','child-failure']:target.write_bytes(b'preserve destination')
  flags=['--overwrite'] if target.exists() or mode.startswith('late-alias') else []
  env=dict(c.ENV,PATH=str(shim)+':'+c.ENV['PATH']);cwd,cmd=c.ENTRIES[id]
  proc=subprocess.Popen([*cmd,str(source),'-o',str(target),*flags],cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  descendants=[]
  try:
   deadline=time.monotonic()+15
   while not ready.exists() and proc.poll() is None and time.monotonic()<deadline:time.sleep(.01)
   require(ready.exists(),'child did not reach barrier')
   descendants=psutil.Process(proc.pid).children(recursive=True)
   if mode=='collision':target.write_bytes(b'late collision')
   elif mode=='late-alias-hard':os.link(source,target)
   elif mode=='late-alias-sym':target.symlink_to(source)
   elif mode=='cancel-int':proc.send_signal(signal.SIGINT)
   elif mode=='cancel-term':proc.send_signal(signal.SIGTERM)
   if mode.startswith('cancel'):
    stdout,stderr=proc.communicate(timeout=6)
   else:release.touch();stdout,stderr=proc.communicate(timeout=15)
   require(proc.returncode!=0,'race incorrectly succeeded')
   if mode=='collision':require(target.read_bytes()==b'late collision','late destination changed')
   elif mode.startswith('late-alias'):require(source.read_bytes()==good,'input changed');require(target.read_bytes()==good,'alias changed')
   else:require(target.read_bytes()==b'preserve destination','old output changed')
   leftovers=[q.name for q in work.iterdir() if q.name not in ['tools','ready','release','child.pid','input.wav','out.wav']]
   require(not leftovers,f'left staging {leftovers}')
   if mode.startswith('cancel'):
    live=[q.pid for q in descendants if q.is_running() and q.status()!=psutil.STATUS_ZOMBIE];require(not live,f'child still alive {live}')
   return dict(exit=proc.returncode,stderr=stderr.decode(errors='replace')[-300:])
  finally:
   if proc.poll() is None:proc.kill();proc.wait()
   # Only PIDs obtained from our process tree; never match names or touch peers.
   for child in descendants:
    with contextlib.suppress(psutil.NoSuchProcess):
     if child.is_running():child.kill()
   release.touch()
 for mode in ['collision','late-alias-hard','late-alias-sym','child-failure','cancel-int','cancel-term']:record(id,'process-'+mode,lambda mode=mode:race(mode))

for id in entries:
 d=root/id
 if d.exists():shutil.rmtree(d) # generated task-owned fixture/output directory only
 d.mkdir()
 safety(id,d)
 rates=[('default',1,[]),*[(f'speed-{v}',v/100,['--speed',v]) for v in [25,50,73.5,100,125.5,200,400]],*[(f'cents-{v}',2**(v/1200),['--cents',v]) for v in [-2400,-1200,-300,0,300,1200,2400]]]
 for name,ratio,flags in rates:
  record(id,'mixed-tone-'+name,lambda name=name,ratio=ratio,flags=flags:audio_case(id,d,name,[f/'mono-44100.wav',f/'stereo-32000.wav'],ratio,flags,tone=True))
 for kind in ['silence','noise','impulse','sweep']:
  for ratio in [.25,.5,.735,1,1.255,2,4]:
   name=f'{kind}-{ratio}'
   record(id,'waveform-'+name,lambda kind=kind,ratio=ratio,name=name:audio_case(id,d,name,[f/f'{kind}-44100.wav'],ratio))
 for rate in [32000,44100,48000]:
  for dur in [.005,.020]:
   for ratio in [.25,1,4]:
    name=f'short-{rate}-{dur}-{ratio}';inputs=[f/f'short-{rate}-{dur}.wav']
    record(id,name,lambda inputs=inputs,name=name,ratio=ratio:audio_case(id,d,name,inputs,ratio))
 for infmt in ['wav','mp3']:
  for ratio in [.5,1,1.255,2]:
   name=f'mp3-{infmt}-{ratio}'
   record(id,'quality-'+name,lambda name=name,infmt=infmt,ratio=ratio:audio_case(id,d,name,[f/f'mono-44100.{infmt}',f/f'stereo-32000.{infmt}'],ratio,extension='mp3',tone=True))
 # First audio stream and source/chapter metadata stripping are independently
 # exercised, not inferred from global tags or the primary graph.
 multi=d/'streams.mka'
 c.tool([c.FF,'-v','error','-i',f/'mono-44100.wav','-i',f/'stereo-32000.wav','-map','0:a:0','-map','1:a:0','-c:a','pcm_s16le',multi])
 record(id,'first-audio-stream',lambda:audio_case(id,d,'first-stream',[multi]))
 metadata=d/'chapters.txt';metadata.write_text(';FFMETADATA1\ntitle=SourceUniqueTitle\nartist=SourceUniqueArtist\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=500\ntitle=UniqueChapterTitle\n')
 chapter=d/'chapter.mp3';c.tool([c.FF,'-v','error','-i',f/'second.wav','-f','ffmetadata','-i',metadata,'-map_metadata','1','-map_chapters','1','-c:a','libmp3lame','-b:a','320k',chapter])
 require(len(c.probe(chapter)['chapters'])==1,'chapter fixture invalid')
 for ext in ['wav','mp3']:
  def tags(ext=ext):
   out=d/('stripped.'+ext);r=c.run(id,[chapter,'-o',out]);require(r.returncode==0,r.stderr.decode());meta=c.probe(out)
   require(not meta.get('chapters'),'source chapters copied');text=json.dumps(meta)
   require(not any(s in text for s in ['SourceUniqueTitle','SourceUniqueArtist','UniqueChapterTitle']),'source tags copied');return meta
  record(id,'metadata-and-chapters-'+ext,tags)
 # Boundary diagnostics are excluded from ordinary quality/speed counts.
 for rate in [32000,44100,48000]:
  for frames in [1,10,64]:
   for ratio in [.25,1,4]:
    name=f'boundary-{rate}-{frames}-{ratio}'
    def boundary(rate=rate,frames=frames,ratio=ratio,name=name):
     src=f/f'boundary-{rate}-{frames}.wav';ref=d/(name+'-ref.wav');c.reference([src],ref,ratio);x,_,_=c.decode(ref)
     out=d/(name+'.wav');out.write_bytes(b'preserve boundary');r=c.run(id,[src,'-o',out,'--speed',ratio*100,'--overwrite'])
     if len(x)==0:
      if r.returncode==0:
       y,sr,_=c.decode(out);require(len(y)>0 and np.isfinite(y).all(),'reference empty: header-only/nonfinite success')
       require(abs(len(y)/sr-frames/rate/ratio)<=.005,'extended boundary duration failure')
       return dict(reference_frames=0,candidate_frames=len(y),extension_beyond_reference=True,excluded_from_speed=True)
      require(out.read_bytes()==b'preserve boundary','reference-empty failure changed destination')
     else:
      require(r.returncode==0,r.stderr.decode());y,_,_=c.decode(out);require(len(y)>0,'header-only output')
     return dict(reference_frames=len(x),exit=r.returncode,excluded_from_speed=True)
    record(id,name,boundary,'boundary')
print('CORRECTNESS COMPLETE',flush=True)
