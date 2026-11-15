// Profile reference calls the pinned shared DSP/encoder without the CLI's file
// validation, atomic-write path or canonical adapter. Semantic gates independently
// measure tones, duration, channels and finite samples.
import { spawnSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const root=process.env.RESAMPLE_NODE_ROOT;
const {renderPcm,concatenatePcm,encodeWav}=await import(pathToFileURL(root+'/src/audio/processing.ts'));
const {encodeAudioBufferToMp3}=await import(pathToFileURL(root+'/src/workers/mp3Worker.ts'));
const [output,speed,...inputs]=process.argv.slice(2);
const tracks=[];
for(const input of inputs){
 const p=spawnSync('ffprobe',['-v','error','-select_streams','a:0','-show_entries','stream=channels','-of','json',input]);
 if(p.status!==0)throw Error(p.stderr.toString());
 const channels=JSON.parse(p.stdout).streams[0].channels;
 const d=spawnSync('ffmpeg',['-v','error','-i',input,'-map','0:a:0','-ar','44100','-c:a','pcm_f32le','-f','f32le','-'],{maxBuffer:512*1024*1024});
 if(d.status!==0)throw Error(d.stderr.toString());
 const pcm=Array.from({length:channels},()=>new Float32Array(d.stdout.length/(4*channels)));
 for(let i=0;i<pcm[0].length;i++)for(let k=0;k<channels;k++)pcm[k][i]=d.stdout.readFloatLE((i*channels+k)*4);
 tracks.push(renderPcm({sampleRate:44100,channels:pcm},{pitchCents:0,speedPercent:Number(speed),linkMode:'speed'}));
}
const pcm=concatenatePcm(tracks);
writeFileSync(output,output.endsWith('.wav')?encodeWav(pcm):new Uint8Array(await encodeAudioBufferToMp3(pcm.channels[0],pcm.channels[1],44100,320).arrayBuffer()));
