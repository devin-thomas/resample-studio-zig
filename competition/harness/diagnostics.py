"""Retain independent evidence for the short Node MP3 qualification failure."""
import json
import pathlib
import common as c

def inspect_short_mp3():
 destination=c.WORK/'node-short-diagnostic.json'
 if destination.exists() and 'declared_mp3_probe' in json.loads(destination.read_text()):return
 out=c.WORK/'node-short-diagnostic.mp3'
 out.unlink(missing_ok=True)
 result=c.run('node',[c.WORK/'fixtures/short.wav','-o',out])
 evidence=dict(returncode=result.returncode,stderr=result.stderr.decode(errors='replace'),ffmpeg=c.FF,ffprobe=c.FP)
 if result.returncode==0:
  evidence['default_probe']=c.probe(out,declared=False)
  evidence['declared_mp3_probe']=c.probe(out)
  evidence['mp3_packets']=json.loads(c.tool([c.FP,'-v','error','-f','mp3','-show_entries','packet=size,duration_time,pos','-of','json',out]))
  data=out.read_bytes();headers=[]
  for offset in range(min(20,len(data)-3)):
   bits=int.from_bytes(data[offset:offset+4],'big')
   if bits>>21==0x7ff:
    headers.append(dict(offset=offset,mpeg_version_bits=(bits>>19)&3,layer_bits=(bits>>17)&3,header_hex=data[offset:offset+4].hex()))
  evidence['leading_sync_headers']=headers
  import subprocess
  command=[c.FF,'-v','error','-f','mp3','-i',str(out),'-f','f32le','-c:a','pcm_f32le','-']
  forced=subprocess.run(command,env=c.ENV,capture_output=True)
  evidence['forced_mp3_decode']=dict(returncode=forced.returncode,pcm_bytes=len(forced.stdout),stderr=forced.stderr.decode(errors='replace'))
  evidence['bytes']=len(data)
 destination.write_text(json.dumps(evidence,indent=2))
 out.unlink(missing_ok=True)
 print('SHORT MP3 DIAGNOSTIC',destination,flush=True)
