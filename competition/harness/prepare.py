"""Build pinned sources outside timing; create identical deterministic fixtures."""
import json,os,pathlib,platform,shutil,subprocess,time,wave
import numpy as np
import jsonschema
import common as c
p=c.arguments(__doc__);p.add_argument('--fixtures-only',action='store_true');p.add_argument('--fixture-ffmpeg',type=pathlib.Path);a=p.parse_args();w=a.work.resolve();w.mkdir(exist_ok=True,parents=True)
# Thread budget is host-default for all entries. No candidate-only filter flags.
env={'PATH':'/Users/research/homebrew/bin:'+os.environ['PATH'],'LC_ALL':'C','LANG':'C','TZ':'UTC','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1'}
ff=shutil.which('ffmpeg',path=env['PATH']);fp=shutil.which('ffprobe',path=env['PATH']);node=shutil.which('node',path=env['PATH'])
cfg=dict(ffmpeg=ff,ffprobe=fp,fixture_generator_ffmpeg=str(a.fixture_ffmpeg.resolve()) if a.fixture_ffmpeg else ff,node=node,environment=env,platform=platform.platform(),architecture=platform.machine(),candidate_revision=c.CANDIDATE_REV,zig_revision=c.ZIG_REV,node_revision=c.NODE_REV)
(w/'config.json').write_text(json.dumps(cfg,indent=2));c.setup(w)
metadata={}
for label,cmd in [('ffmpeg',[ff,'-version']),('ffmpeg-build',[ff,'-buildconf']),('ffprobe',[fp,'-version']),('node',[node,'--version']),('clang',['clang','--version']),('go',['go','version']),('rust',['rustc','-vV']),('cargo',['cargo','--version']),('host',['sysctl','hw.model','hw.ncpu','hw.memsize','hw.physicalcpu']),('os',['sw_vers']),('disk',['df','-h',str(w)]),('load',['ps','-axo','pid,ppid,%cpu,rss,etime,command','-r'])]:
 r=subprocess.run(cmd,env=c.ENV,capture_output=True,text=True);metadata[label]=r.stdout
metadata['ffmpeg-sha256']=c.sha(pathlib.Path(ff).resolve());metadata['ffprobe-sha256']=c.sha(pathlib.Path(fp).resolve())
if not a.fixtures_only:
 builds=[]
 zig=os.environ.get('ZIG','/Volumes/PortableSSD/scratch/resample-studio-zig-tools/zig-aarch64-macos-0.15.2/zig')
 metadata['zig']=subprocess.check_output([zig,'version'],text=True)
 items=[('zig',w/'zig',[zig,'build-exe','src/main.zig','-target','aarch64-macos.14.0','-O','ReleaseSafe','-femit-bin=resample'])]
 schema=json.loads((c.ROOT/'competition/entry.schema.json').read_text())
 for id in c.IDS[1:-1]:
  m=json.loads((w/f'candidates/implementations/{id}/entry.json').read_text());jsonschema.validate(m,schema)
  items.append((id,w/'candidates'/m['cwd'],m['build']))
 items.append(('node',w/'node',['npm','exec','--yes','pnpm@10.11.0','--','install','--frozen-lockfile']))
 for id,cwd,cmd in items:
  start=time.perf_counter();r=subprocess.run(cmd,cwd=cwd,env=c.ENV,capture_output=True,text=True);seconds=time.perf_counter()-start
  (w/f'{id}-build.log').write_text(r.stdout+r.stderr)
  exe=c.ENTRIES[id][1][0]
  builds.append(dict(id=id,argv=cmd,cwd=str(cwd),seconds=seconds,returncode=r.returncode,binary_bytes=pathlib.Path(exe).stat().st_size,binary_sha256=c.sha(pathlib.Path(exe))))
  print('BUILD',id,r.returncode,round(seconds,3),flush=True)
  (w/'builds.json').write_text(json.dumps(builds,indent=2))
  if r.returncode:raise RuntimeError(r.stderr)
 metadata['node-lock-sha256']=c.sha(w/'node/pnpm-lock.yaml')
metadata['fixture_generator']={'path':cfg['fixture_generator_ffmpeg'],'version':subprocess.check_output([cfg['fixture_generator_ffmpeg'],'-version'],text=True,stderr=subprocess.STDOUT),'build_configuration':subprocess.check_output([cfg['fixture_generator_ffmpeg'],'-buildconf'],text=True,stderr=subprocess.STDOUT),'sha256':c.sha(pathlib.Path(cfg['fixture_generator_ffmpeg']).resolve())}
(w/'environment.json').write_text(json.dumps(metadata,indent=2))
f=w/'fixtures';f.mkdir(exist_ok=True);fixture_ff=cfg['fixture_generator_ffmpeg']
def wav(name,rate=48000,seconds=1,channels=2,kind='tone',freq=440,frames=None):
 dest=f/(name+'.wav')
 if dest.exists():return dest
 n=round(seconds*rate) if frames is None else frames
 rng=np.random.default_rng(20261115)
 with wave.open(str(dest),'wb') as out:
  out.setparams((channels,2,rate,0,'NONE','not compressed'))
  for offset in range(0,n,rate):
   i=np.arange(offset,min(n,offset+rate));t=i/rate
   if kind=='silence':x=np.zeros((len(i),channels))
   elif kind=='noise':x=rng.uniform(-0.2,0.2,(len(i),channels))
   elif kind=='impulse':x=np.where((i==0)|(i==n//2)|(i==n-1),0.5,0)[:,None]*np.ones((1,channels))
   elif kind=='sweep':x=(0.15*np.sin(2*np.pi*(100*t+(0.45*rate-100)*t*t/(2*seconds))))[:,None]*np.ones((1,channels))
   else:x=np.stack([0.2*np.sin(2*np.pi*(freq if k==0 else freq*1.5)*t) for k in range(channels)],axis=1)
   out.writeframes(np.round(x*32767).astype('<i2').tobytes())
 return dest
for name,duration in [('short',.020),('second',1),('minute',60),('ten-minute',600),('half-minute',30),('sixteenth',3.75)]:
 src=wav(name,seconds=duration)
 dst=src.with_suffix('.mp3')
 if not dst.exists():c.tool([fixture_ff,'-v','error','-i',src,'-map_metadata','-1','-c:a','libmp3lame','-b:a','320k',dst],timeout=600)
for rate in [32000,44100,48000]:
 for dur in [.005,.020]:wav(f'short-{rate}-{dur}',rate=rate,seconds=dur,channels=1)
 for n in [1,10,64]:wav(f'boundary-{rate}-{n}',rate=rate,frames=n,channels=1)
 for kind in ['silence','noise','impulse','sweep']:wav(f'{kind}-{rate}',rate=rate,kind=kind)
 wav(f'mono-{rate}',rate=rate,channels=1,freq=440)
 wav(f'stereo-{rate}',rate=rate,channels=2,freq=880)
for name in ['mono-44100','stereo-32000']:
 if not (f/(name+'.mp3')).exists():c.tool([fixture_ff,'-v','error','-i',f/(name+'.wav'),'-c:a','libmp3lame','-b:a','320k',f/(name+'.mp3')])
manifest=[dict(file=q.name,bytes=q.stat().st_size,sha256=c.sha(q)) for q in sorted(f.iterdir()) if q.is_file()]
(w/'fixtures.json').write_text(json.dumps(manifest,indent=2));print('FIXTURES',len(manifest),flush=True)
