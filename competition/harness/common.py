"""Shared executable discovery and deterministic, unmodified process environment."""
import argparse, hashlib, json, os, pathlib, subprocess, time
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[2]
IDS = ['zig', 'c', 'cpp', 'go', 'rust', 'node']
ZIG_REV = '5ab090b54caf4efd9b0c2712571bfd20b24e5554'
NODE_REV = '7c6e7a91bacabb63eb2895af5ec0101a812fce33'
CANDIDATE_REV = 'ebb4fb2b19b68c0a4cce02b5b125c8e4be1c349a'

def arguments(description):
 p=argparse.ArgumentParser(description=description)
 p.add_argument('--work',type=pathlib.Path,required=True)
 return p

def setup(work):
 global WORK, ENV, ENTRIES, FF, FP
 WORK=work.resolve(); WORK.mkdir(parents=True,exist_ok=True)
 cfg=json.loads((WORK/'config.json').read_text())
 ENV=dict(os.environ, **cfg['environment'])
 ENV['RESAMPLE_NODE_ROOT']=str(WORK/'node')
 FF=cfg['ffmpeg'];FP=cfg['ffprobe']; ENTRIES={}
 for id in IDS:
  if id=='zig':cwd=WORK/'zig';cmd=[str(cwd/'resample')]
  elif id=='node':cwd=WORK/'node';cmd=[cfg['node'],'--import',str(cwd/'node_modules/tsx/dist/loader.mjs'),str(ROOT/'competition/harness/node-adapter.mjs')]
  else:
   manifest=json.loads((WORK/f'candidates/implementations/{id}/entry.json').read_text())
   cwd=WORK/'candidates'/manifest['cwd'];cmd=[str(cwd/manifest['run'][0]),*manifest['run'][1:]]
  ENTRIES[id]=(cwd,cmd)
 return cfg

def run(id,args,timeout=180,env=None,cwd=None):
 d,cmd=ENTRIES[id]
 return subprocess.run([*cmd,*map(str,args)],cwd=cwd or d,env=env or ENV,capture_output=True,timeout=timeout)

def tool(args,timeout=180):
 r=subprocess.run(list(map(str,args)),env=ENV,capture_output=True,timeout=timeout)
 if r.returncode:raise RuntimeError(r.stderr.decode(errors='replace')[-3000:])
 return r.stdout

def probe(p):return json.loads(tool([FP,'-v','error','-show_streams','-show_format','-show_chapters','-of','json',p]))
def decode(p):
 info=probe(p);s=next(s for s in info['streams'] if s['codec_type']=='audio')
 a=np.frombuffer(tool([FF,'-v','error','-i',p,'-map','0:a:0','-f','f32le','-c:a','pcm_f32le','-'],timeout=600),dtype='<f4').reshape(-1,int(s['channels']))
 return a,int(s['sample_rate']),info

def graph(n,ratio,rate=48000):
 return ';'.join(f'[{i}:a:0]aresample={rate},asetrate={rate*ratio:.8f},aresample={rate},aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a{i}]' for i in range(n))+';'+''.join(f'[a{i}]' for i in range(n))+f'concat=n={n}:v=0:a=1[out]'

def reference(inputs,out,ratio=1):
 cmd=[FF,'-nostdin','-v','error','-y']
 for p in inputs:cmd += ['-i',str(p)]
 cmd += ['-filter_complex',graph(len(inputs),ratio),'-map','[out]','-map_metadata','-1','-map_chapters','-1']
 cmd += ['-c:a','pcm_s24le','-f','wav'] if out.suffix.lower()=='.wav' else ['-c:a','libmp3lame','-b:a','320k','-f','mp3']
 tool([*cmd,out],timeout=600)
 return out

def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()

def append(p,item):
 with open(p,'a') as f:f.write(json.dumps(item,allow_nan=False)+'\n');f.flush()

def readlines(p):return [json.loads(s) for s in p.read_text().splitlines()] if p.exists() else []
