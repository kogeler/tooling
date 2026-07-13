// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"io"
	"sync"
	"time"

	"github.com/go-telegram/bot"
	"github.com/go-telegram/bot/models"
)

// --- fakeClock ---------------------------------------------------------------
//
// Deterministic clock: After/Sleep advance time instantly and never block, so
// retry/backoff/grace logic runs in microseconds. Tests that install it via
// swapClock must not run in parallel.

type fakeClock struct {
	mu  sync.Mutex
	now time.Time
	// slept records every requested wait, in order.
	slept []time.Duration
}

func newFakeClock() *fakeClock {
	return &fakeClock{now: time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)}
}

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *fakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = c.now.Add(d)
}

func (c *fakeClock) After(d time.Duration) <-chan time.Time {
	c.mu.Lock()
	c.now = c.now.Add(d)
	c.slept = append(c.slept, d)
	now := c.now
	c.mu.Unlock()
	ch := make(chan time.Time, 1)
	ch <- now
	return ch
}

func (c *fakeClock) Sleep(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = c.now.Add(d)
	c.slept = append(c.slept, d)
}

// swapClock installs a fake clock and returns a restore func for t.Cleanup.
func swapClock(c Clock) func() {
	old := clk
	clk = c
	return func() { clk = old }
}

// --- fakeSender ----------------------------------------------------------------

type sentMessage struct {
	ChatID int64
	Text   string
}

// fakeSender records every SendMessage call and answers via the script hook.
// A nil script means every send succeeds.
type fakeSender struct {
	mu     sync.Mutex
	sent   []sentMessage
	script func(call int, chatID int64, text string) error
	calls  int
}

func (f *fakeSender) SendMessage(ctx context.Context, params *bot.SendMessageParams) (*models.Message, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	f.mu.Lock()
	call := f.calls
	f.calls++
	chatID, _ := params.ChatID.(int64)
	f.sent = append(f.sent, sentMessage{ChatID: chatID, Text: params.Text})
	script := f.script
	f.mu.Unlock()
	if script != nil {
		if err := script(call, chatID, params.Text); err != nil {
			return nil, err
		}
	}
	return &models.Message{}, nil
}

func (f *fakeSender) sentTo(chatID int64) []sentMessage {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []sentMessage
	for _, m := range f.sent {
		if m.ChatID == chatID {
			out = append(out, m)
		}
	}
	return out
}

// --- fakeAT ------------------------------------------------------------------
//
// Command-level fake for pipeline/diagnostics tests (the byte-level scripted
// port below is for SimpleAT framing tests).

type fakeATResp struct {
	lines []string
	err   error
}

type fakeAT struct {
	mu sync.Mutex
	// queued responses per exact command; when a queue is exhausted the last
	// response repeats.
	responses map[string][]fakeATResp
	calls     []string
}

func newFakeAT() *fakeAT {
	return &fakeAT{responses: make(map[string][]fakeATResp)}
}

func (f *fakeAT) on(cmd string, lines []string, err error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.responses[cmd] = append(f.responses[cmd], fakeATResp{lines: lines, err: err})
}

func (f *fakeAT) Command(cmd string) ([]string, error) {
	return f.CommandWithTimeout(cmd, time.Second)
}

func (f *fakeAT) CommandWithTimeout(cmd string, _ time.Duration) ([]string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, cmd)
	queue := f.responses[cmd]
	if len(queue) == 0 {
		// Unscripted commands succeed with no payload (e.g. ATE0, AT+CMGD).
		return nil, nil
	}
	resp := queue[0]
	if len(queue) > 1 {
		f.responses[cmd] = queue[1:]
	}
	return resp.lines, resp.err
}

func (f *fakeAT) Ping() error {
	_, err := f.CommandWithTimeout("AT", 2*time.Second)
	return err
}

func (f *fakeAT) commandCount(cmd string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := 0
	for _, c := range f.calls {
		if c == cmd {
			n++
		}
	}
	return n
}

// --- scriptedPort --------------------------------------------------------------
//
// Byte-level serial port fake. Each Read consumes one scripted chunk; an empty
// queue behaves like a VTIME timeout (0 bytes, io.EOF), matching tarm/serial
// over os.File. Writes are recorded and can enqueue response chunks.

type readChunk struct {
	data []byte
	err  error
}

type scriptedPort struct {
	mu      sync.Mutex
	chunks  []readChunk
	writes  []string
	onWrite func(written string) []readChunk
}

// chunk builds a data chunk; use eofChunk() to simulate an idle read timeout.
func chunk(s string) readChunk   { return readChunk{data: []byte(s)} }
func eofChunk() readChunk        { return readChunk{err: io.EOF} }
func errChunk(e error) readChunk { return readChunk{err: e} }

func (p *scriptedPort) enqueue(chunks ...readChunk) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.chunks = append(p.chunks, chunks...)
}

func (p *scriptedPort) Read(b []byte) (int, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if len(p.chunks) == 0 {
		return 0, io.EOF // idle: VTIME expired with no data
	}
	ch := p.chunks[0]
	p.chunks = p.chunks[1:]
	n := copy(b, ch.data)
	if n < len(ch.data) {
		// Remainder stays queued for the next read.
		p.chunks = append([]readChunk{{data: ch.data[n:], err: ch.err}}, p.chunks...)
		return n, nil
	}
	return n, ch.err
}

func (p *scriptedPort) Write(b []byte) (int, error) {
	p.mu.Lock()
	written := string(b)
	p.writes = append(p.writes, written)
	hook := p.onWrite
	p.mu.Unlock()
	if hook != nil {
		if resp := hook(written); resp != nil {
			p.enqueue(resp...)
		}
	}
	return len(b), nil
}
