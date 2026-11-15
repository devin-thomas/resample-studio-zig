package main

import (
	"strings"
	"testing"
)

func TestCanonicalArguments(t *testing.T) {
	for _, args := range [][]string{
		{"in", "-o", "out.wav", "--speed", "NaN"},
		{"in", "-o", "out.wav", "--speed", "Infinity"},
		{"in", "-o", "out.wav", "--speed", "0x1p6"},
		{"in", "-o", "out.wav", "--speed", "100x"},
		{"in", "-o", "out.wav", "--speed", "24.999"},
		{"in", "-o", "out.wav", "--speed", "400.001"},
		{"in", "-o", "out.wav", "--cents", "-2400.001"},
		{"in", "-o", "out.wav", "--cents", "2400.001"},
		{"in", "-o", "out.wav", "--cents", "0", "--speed", "100"},
		{"in", "-o", "out.wav", "--speed", "100", "--speed", "100"},
		{"in", "-o", "out.wav", "--output", "other.wav"},
		{"in", "-o", "out.wav", "--unknown"},
		{"in", "-o", "out.wav", "--speed"},
		{"in", "-o", "out.ogg"},
		{"-o", "out.wav"}, {"in"},
	} {
		if _, err := parse(args); err == nil {
			t.Errorf("accepted invalid arguments %q", args)
		}
	}
	o, err := parse([]string{"first.wav", "--speed", "73.5", "-o", "out.WAV", "--", "-second.wav"})
	if err != nil || o.ratio != .735 || strings.Join(o.inputs, ",") != "first.wav,-second.wav" {
		t.Fatalf("unexpected parse: %+v, %v", o, err)
	}
	for _, cents := range []string{"-2400", "-1200", "-300", "0", "300", "1200", "2400"} {
		if _, err := parse([]string{"in", "-o", "out.mp3", "--cents", cents}); err != nil {
			t.Fatal(err)
		}
	}
}

func TestBoundedDiagnosticTail(t *testing.T) {
	var b diagnosticTail
	want := ""
	for _, count := range []int{0, 1, 1000, 16000, 5, 32768, 0, 16383, 30} {
		chunk := strings.Repeat("0123456789", count/10) + strings.Repeat("x", count%10)
		b.Write([]byte(chunk))
		want += chunk
		if len(want) > diagnosticLimit {
			want = want[len(want)-diagnosticLimit:]
		}
		if b.String() != want {
			t.Fatalf("diagnostic ring differs after %d-byte write", count)
		}
	}
	chunk := []byte(strings.Repeat("error", 1000))
	if allocs := testing.AllocsPerRun(1000, func() { b.Write(chunk) }); allocs != 0 {
		t.Fatalf("diagnostic writes allocated %g times per write", allocs)
	}
}
