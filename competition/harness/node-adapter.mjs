// Canonical contract adapter. Validation/translation only; the pinned CLI owns
// decoding, linked DSP, encoding, durability and publication. No audio transform.
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const args = process.argv.slice(2);
try {
  if (!args.length || (args.length === 1 && ['-h', '--help'].includes(args[0]))) {
    console.log('Usage: resample [INPUT ...] -o OUTPUT.wav|OUTPUT.mp3 [--speed PERCENT | --cents CENTS] [--overwrite] [-- INPUT ...]');
  } else if (args.length === 1 && args[0] === '--version') {
    console.log('resample Node 7c6e7a9 canonical adapter v1');
  } else {
    let output, rate, overwrite = false, positional = false;
    const inputs = [];
    for (let i = 0; i < args.length; i++) {
      const a = args[i];
      if (positional) { inputs.push(a); continue; }
      if (a === '--') { positional = true; continue; }
      if (a === '--overwrite') { overwrite = true; continue; }
      if (a === '-o' || a === '--output') {
        if (output !== undefined) throw Error('duplicate output');
        if (++i >= args.length) throw Error('missing output');
        output = args[i]; continue;
      }
      if (a === '--speed' || a === '--cents') {
        if (rate !== undefined) throw Error('duplicate or combined rate');
        if (++i >= args.length) throw Error('missing rate');
        const value = args[i];
        if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value)) throw Error('rate must be finite decimal');
        const n = Number(value), cents = a === '--cents';
        if (!Number.isFinite(n) || n < (cents ? -2400 : 25) || n > (cents ? 2400 : 400)) throw Error('rate out of range');
        rate = cents ? 100 * 2 ** (n / 1200) : n; continue;
      }
      if (a.startsWith('-')) throw Error(`unknown option ${a}`);
      inputs.push(a);
    }
    if (!inputs.length || output === undefined) throw Error('inputs and exactly one output are required');
    if (!/\.(wav|mp3)$/i.test(output)) throw Error('output requires WAV or MP3 extension');
    const main = path.resolve(process.env.RESAMPLE_NODE_ROOT, 'cli/main.ts');
    process.argv = [process.execPath, main, 'export', '--mode', 'speed', '--speed', String(rate ?? 100), '-o', output, ...(overwrite ? ['--overwrite'] : []), '--', ...inputs];
    await import(pathToFileURL(main).href);
  }
} catch (error) {
  console.error(`resample adapter: ${error.message}`); process.exitCode = 1;
}
