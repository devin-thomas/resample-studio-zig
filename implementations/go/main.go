// resample-go manages a streaming FFmpeg render and atomic local-file publication.
package main

import (
	"context"
	"errors"
	"fmt"
	"math"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const usage = `Usage: resample [INPUT ...] [-o|--output OUTPUT.wav|OUTPUT.mp3]
                [--speed PERCENT | --cents CENTS] [--overwrite] [-- INPUT ...]

Inputs concatenate in order. Pitch, speed and duration change together.
  --speed PERCENT  Finite decimal 25..400 (default 100)
  --cents CENTS    Finite decimal -2400..2400, exclusive with --speed
  -o, --output     Required output: stereo 48 kHz, 24-bit WAV or 320 kbps MP3
  --overwrite      Replace an existing output atomically
  -h, --help       Show usage
  --version        Show version
  --               Treat remaining tokens as input filenames

Requires FFmpeg with libmp3lame on PATH. Local regular files only.
`

type options struct {
	inputs    []string
	output    string
	ratio     float64
	overwrite bool
	action    string
}

// ParseFloat accepts hexadecimal floats and nonfinite values; the CLI accepts
// only decimal notation (including decimal exponents).
var decimal = regexp.MustCompile(`^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$`)

func parse(args []string) (options, error) {
	o := options{ratio: 1, action: "render"}
	if len(args) == 0 {
		o.action = "help"
		return o, nil
	}
	rateSet, outputSet, literal := false, false, false
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if literal {
			o.inputs = append(o.inputs, arg)
			continue
		}
		switch arg {
		case "--":
			literal = true
		case "-h", "--help":
			o.action = "help"
		case "--version":
			o.action = "version"
		case "--overwrite":
			o.overwrite = true
		case "-o", "--output":
			if outputSet {
				return o, errors.New("specify the output exactly once")
			}
			outputSet = true
			i++
			if i == len(args) || args[i] == "" {
				return o, errors.New("provide a filename after --output")
			}
			o.output = args[i]
		case "--speed", "--cents":
			if rateSet {
				return o, errors.New("choose --speed or --cents, exactly once")
			}
			rateSet = true
			i++
			if i == len(args) {
				return o, fmt.Errorf("provide a decimal value after %s", arg)
			}
			v, err := strconv.ParseFloat(args[i], 64)
			if !decimal.MatchString(args[i]) || err != nil || math.IsNaN(v) || math.IsInf(v, 0) {
				return o, fmt.Errorf("%s requires a finite decimal value", arg)
			}
			if arg == "--speed" {
				if v < 25 || v > 400 {
					return o, errors.New("--speed must be 25..400")
				}
				o.ratio = v / 100
			} else {
				if v < -2400 || v > 2400 {
					return o, errors.New("--cents must be -2400..2400")
				}
				o.ratio = math.Exp2(v / 1200)
			}
		default:
			if strings.HasPrefix(arg, "-") {
				return o, fmt.Errorf("unknown option %q; use -- before leading-dash filenames", arg)
			}
			o.inputs = append(o.inputs, arg)
		}
	}
	if o.action != "render" {
		return o, nil
	}
	if len(o.inputs) == 0 {
		return o, errors.New("provide at least one input file")
	}
	if !outputSet {
		return o, errors.New("provide -o OUTPUT.wav or OUTPUT.mp3")
	}
	ext := strings.ToLower(filepath.Ext(o.output))
	if ext != ".wav" && ext != ".mp3" {
		return o, errors.New("output extension must be .wav or .mp3")
	}
	return o, nil
}

type files struct {
	inputs []*os.File
	stats  []os.FileInfo
	output string
}

func (f files) close() {
	for _, input := range f.inputs {
		input.Close()
	}
}

func validate(o options) (files, error) {
	var f files
	abs, err := filepath.Abs(o.output)
	if err != nil {
		return f, err
	}
	parent, err := filepath.EvalSymlinks(filepath.Dir(abs))
	if err != nil {
		return f, fmt.Errorf("output parent: %w", err)
	}
	f.output = filepath.Join(parent, filepath.Base(abs))
	for _, input := range o.inputs {
		if input == "" {
			return f, errors.New("input filename must not be empty")
		}
		path, err := filepath.Abs(input)
		if err != nil {
			return f, err
		}
		// Inspect the opened inode, then retain it for FFmpeg. O_NONBLOCK
		// prevents a pathname changed into a FIFO from blocking validation.
		file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NONBLOCK, 0)
		if err != nil {
			return f, fmt.Errorf("cannot read input %q: %w", input, err)
		}
		f.inputs = append(f.inputs, file)
		info, err := file.Stat()
		if err != nil {
			return f, fmt.Errorf("cannot stat input %q: %w", input, err)
		}
		if !info.Mode().IsRegular() || info.Size() == 0 {
			return f, fmt.Errorf("input %q must be a nonempty local regular file", input)
		}
		if path == f.output {
			return f, errors.New("output must differ from every input")
		}
		f.stats = append(f.stats, info)
	}
	return f, f.checkOutput(o.overwrite)
}

func (f files) checkOutput(overwrite bool) error {
	entry, err := os.Lstat(f.output)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("cannot inspect output: %w", err)
	}
	info, err := os.Stat(f.output)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("cannot inspect output target: %w", err)
	}
	if info != nil {
		for _, input := range f.stats {
			if os.SameFile(info, input) {
				return errors.New("output aliases an input (including symlinks or hard links)")
			}
		}
	}
	if !entry.Mode().IsRegular() && entry.Mode()&os.ModeSymlink == 0 {
		return errors.New("output must be a regular file or symlink")
	}
	if !overwrite {
		return errors.New("output exists; use --overwrite to replace it")
	}
	return nil
}

func filterGraph(count int, ratio float64) string {
	var graph strings.Builder
	graph.Grow(count * 170)
	for i := 0; i < count; i++ {
		fmt.Fprintf(&graph, "[%d:a:0]aresample=48000,asetrate=%.8f,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a%d];", i, 48000*ratio, i)
	}
	for i := 0; i < count; i++ {
		fmt.Fprintf(&graph, "[a%d]", i)
	}
	fmt.Fprintf(&graph, "concat=n=%d:v=0:a=1[out]", count)
	return graph.String()
}

// Retain only the tail of child diagnostics; malformed media cannot grow the
// Go parent's memory in proportion to the track or diagnostic volume.
const diagnosticLimit = 16384

type diagnosticTail struct {
	data [diagnosticLimit]byte
	next int
	size int
}

func (b *diagnosticTail) Write(p []byte) (int, error) {
	n := len(p)
	if n >= diagnosticLimit {
		copy(b.data[:], p[n-diagnosticLimit:])
		b.next, b.size = 0, diagnosticLimit
		return n, nil
	}
	first := copy(b.data[b.next:], p)
	copy(b.data[:], p[first:])
	b.next = (b.next + n) % diagnosticLimit
	b.size = min(diagnosticLimit, b.size+n)
	return n, nil
}

func (b *diagnosticTail) String() string {
	if b.size < diagnosticLimit {
		return string(b.data[:b.size])
	}
	return string(b.data[b.next:]) + string(b.data[:b.next])
}

func render(ctx context.Context, o options) (result error) {
	f, err := validate(o)
	defer f.close()
	if err != nil {
		return err
	}
	ffmpeg, err := exec.LookPath("ffmpeg")
	if err != nil {
		return fmt.Errorf("install FFmpeg with libmp3lame and add it to PATH: %w", err)
	}
	// Reserve an unpredictable sibling name exclusively before invoking FFmpeg.
	tmp, err := os.CreateTemp(filepath.Dir(f.output), ".resample-*.tmp")
	if err != nil {
		return fmt.Errorf("create temporary output: %w", err)
	}
	name := tmp.Name()
	defer tmp.Close()
	defer func() {
		if err := os.Remove(name); err != nil && !errors.Is(err, os.ErrNotExist) {
			result = errors.Join(result, fmt.Errorf("remove temporary output %q: %w", name, err))
		}
	}()
	args := []string{"-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-xerror", "-abort_on", "empty_output+empty_output_stream"}
	for i := range f.inputs {
		// Descriptor paths pin the validated inodes instead of reopening input
		// names. Only local file access is enabled, including nested protocols.
		args = append(args, "-protocol_whitelist", "file", "-i", fmt.Sprintf("/dev/fd/%d", i+4))
	}
	args = append(args, "-filter_complex", filterGraph(len(f.inputs), o.ratio))
	// FFmpeg's concat accepts an empty track when another track is nonempty.
	// Each PCM null output must receive decoded samples, or the global
	// empty_output_stream gate fails. Packet-copy guards are insufficient:
	// compressed packets can decode to zero samples after encoder pre-skip.
	// The decoder is shared with the main graph; only one frame per input is
	// encoded for validation. No probe processes or whole-track buffers.
	for i := range f.inputs {
		args = append(args, "-map", fmt.Sprintf("%d:a:0", i), "-c:a", "pcm_s16le", "-frames:a", "1", "-f", "null", os.DevNull)
	}
	args = append(args, "-map", "[out]", "-map_metadata", "-1", "-map_metadata:s:a", "-1", "-map_chapters", "-1")
	if strings.EqualFold(filepath.Ext(f.output), ".wav") {
		args = append(args, "-c:a", "pcm_s24le", "-f", "wav")
	} else {
		args = append(args, "-c:a", "libmp3lame", "-b:a", "320k", "-f", "mp3")
	}
	// FFmpeg receives the already-open regular file, avoiding a reopen of its
	// temporary pathname. /dev/fd preserves seeking for WAV header finalization.
	args = append(args, "/dev/fd/3")
	cmd := exec.CommandContext(ctx, ffmpeg, args...)
	cmd.ExtraFiles = append([]*os.File{tmp}, f.inputs...)
	// Bound stderr-drain waits if an abnormal executable leaves an inherited
	// pipe open in a descendant. CommandContext kills the direct child through
	// os.Process, whose Wait/Signal synchronization avoids raw-PID reuse races.
	cmd.WaitDelay = 2 * time.Second
	var diagnostic diagnosticTail
	cmd.Stderr = &diagnostic
	err = cmd.Run()
	if ctx.Err() != nil {
		return fmt.Errorf("render cancelled: %w", ctx.Err())
	}
	if err != nil {
		return fmt.Errorf("FFmpeg failed: %w\n%s", err, strings.TrimSpace(diagnostic.String()))
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("close temporary output: %w", err)
	}
	if err := f.checkOutput(o.overwrite); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return fmt.Errorf("render cancelled before publication: %w", err)
	}
	if o.overwrite {
		// Replaces the directory entry, never writes through a destination alias.
		err = os.Rename(name, f.output)
	} else {
		// A hard link is an atomic exclusive publish: EEXIST also protects a
		// destination created during rendering. No full-output copy is needed.
		err = os.Link(name, f.output)
	}
	if err != nil {
		return fmt.Errorf("publish output: %w", err)
	}
	return nil
}

func run() int {
	o, err := parse(os.Args[1:])
	if err == nil {
		switch o.action {
		case "help":
			_, err = fmt.Fprint(os.Stdout, usage)
		case "version":
			_, err = fmt.Fprintln(os.Stdout, "resample-go 0.1.0 (resample-linked-v1)")
		default:
			ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
			defer stop()
			err = render(ctx, o)
		}
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "resample:", err)
		if errors.Is(err, context.Canceled) {
			return 130
		}
		return 1
	}
	return 0
}

func main() { os.Exit(run()) }
