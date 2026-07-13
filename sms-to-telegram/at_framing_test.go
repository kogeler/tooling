// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"errors"
	"strings"
	"testing"
	"time"
)

// newScriptedAT builds a SimpleAT over a scriptedPort with a fake clock
// installed, so idle reads advance time instantly.
func newScriptedAT(t *testing.T, timeout time.Duration) (*SimpleAT, *scriptedPort, *fakeClock) {
	t.Helper()
	fc := newFakeClock()
	t.Cleanup(swapClock(fc))
	port := &scriptedPort{}
	return NewSimpleAT(port, timeout), port, fc
}

// A PDU line split across two reads with an idle timeout between them must be
// reassembled into one line, not committed as two bogus lines.
func TestSimpleAT_PartialLineReassembled(t *testing.T) {
	at, port, _ := newScriptedAT(t, 5*time.Second)
	port.enqueue(
		chunk("+CMGL: 5,1,,24\r\n"),
		chunk("07915348"), // PDU interrupted mid-line...
		eofChunk(),        // ...VTIME expires...
		eofChunk(),
		chunk("74894370000C\r\n"), // ...rest arrives
		chunk("OK\r\n"),
	)

	lines, err := at.Command("AT+CMGL=4")
	if err != nil {
		t.Fatalf("Command() error = %v", err)
	}
	if len(lines) != 2 {
		t.Fatalf("lines = %v, want 2 lines", lines)
	}
	if lines[1] != "0791534874894370000C" {
		t.Errorf("reassembled PDU line = %q", lines[1])
	}
}

// A known single-line URC inside a response must be skipped, not returned as
// a response line.
func TestSimpleAT_URCSkippedInsideResponse(t *testing.T) {
	at, port, _ := newScriptedAT(t, 5*time.Second)
	port.enqueue(
		chunk("+CMGL: 5,1,,24\r\n"),
		chunk("+CMTI: \"SM\",7\r\n"), // URC interleaved between header and PDU
		chunk("0791534874894370\r\n"),
		chunk("OK\r\n"),
	)

	lines, err := at.Command("AT+CMGL=4")
	if err != nil {
		t.Fatalf("Command() error = %v", err)
	}
	if len(lines) != 2 || lines[0] != "+CMGL: 5,1,,24" || lines[1] != "0791534874894370" {
		t.Errorf("lines = %v, want header+PDU without the URC", lines)
	}
}

// A two-line URC (+CMT: header followed by its PDU payload) must be skipped
// entirely: the payload line must not be mistaken for a response line.
func TestSimpleAT_TwoLineURCSkipped(t *testing.T) {
	at, port, _ := newScriptedAT(t, 5*time.Second)
	port.enqueue(
		chunk("+CMT: ,24\r\n"),
		chunk("07915348DEADBEEF\r\n"), // payload of the URC, not a response
		chunk("+CPIN: READY\r\n"),
		chunk("OK\r\n"),
	)

	lines, err := at.Command("AT+CPIN?")
	if err != nil {
		t.Fatalf("Command() error = %v", err)
	}
	if len(lines) != 1 || lines[0] != "+CPIN: READY" {
		t.Errorf("lines = %v, want only +CPIN: READY", lines)
	}
}

// After a command deadline the session is poisoned: the late response must not
// satisfy the next command, which fails fast with ErrSessionPoisoned.
func TestSimpleAT_DeadlinePoisonsSession(t *testing.T) {
	at, port, _ := newScriptedAT(t, 200*time.Millisecond)
	// No data at all: the command times out.
	_, err := at.Command("AT+CMGL=4")
	if !errors.Is(err, ErrModemTimeout) {
		t.Fatalf("first command error = %v, want timeout", err)
	}
	if !at.Poisoned() {
		t.Fatal("session should be poisoned after deadline")
	}

	// The late response arrives now; a healthy session would read it as the
	// answer to the *next* command.
	port.enqueue(chunk("+CMGL: 5,1,,24\r\n"), chunk("OK\r\n"))

	_, err = at.Command("AT")
	if !errors.Is(err, ErrSessionPoisoned) {
		t.Fatalf("second command error = %v, want ErrSessionPoisoned", err)
	}
	if len(port.writes) != 1 {
		t.Errorf("poisoned session wrote %d commands, want 1 (no writes after poison)", len(port.writes))
	}
}

// +CME ERROR is a terminal result line: the command fails, but the session
// stays synchronized and the next command works.
func TestSimpleAT_CMEErrorKeepsSessionUsable(t *testing.T) {
	at, port, _ := newScriptedAT(t, 5*time.Second)
	port.enqueue(chunk("+CME ERROR: SIM busy\r\n"))

	_, err := at.Command("AT+CPIN?")
	if !IsModemError(err) {
		t.Fatalf("error = %v, want modem error", err)
	}
	if at.Poisoned() {
		t.Fatal("CME ERROR must not poison the session")
	}

	port.enqueue(chunk("OK\r\n"))
	if _, err := at.Command("AT"); err != nil {
		t.Fatalf("next command after CME error = %v, want success", err)
	}
}

// A partial-response deadline (some lines received, no terminal OK) must also
// poison the session.
func TestSimpleAT_PartialResponseDeadlinePoisons(t *testing.T) {
	at, _, _ := newScriptedAT(t, 200*time.Millisecond)
	port := at.port.(*scriptedPort)
	port.enqueue(chunk("+CMGL: 5,1,,24\r\n")) // header but never OK

	_, err := at.Command("AT+CMGL=4")
	if !errors.Is(err, ErrModemTimeout) {
		t.Fatalf("error = %v, want timeout", err)
	}
	if !at.Poisoned() {
		t.Fatal("session should be poisoned after incomplete response")
	}
}

// Bare OK without a trailing newline is a deliberate exception and must still
// terminate the command successfully.
func TestSimpleAT_BareOKFragment(t *testing.T) {
	at, port, _ := newScriptedAT(t, time.Second)
	port.enqueue(chunk("OK")) // no newline, then idle

	lines, err := at.Command("AT")
	if err != nil {
		t.Fatalf("Command() error = %v", err)
	}
	if len(lines) != 0 {
		t.Errorf("lines = %v, want none", lines)
	}
	if at.Poisoned() {
		t.Fatal("bare OK must not poison the session")
	}
}

// An arbitrary dangling fragment must never be committed as a line.
func TestSimpleAT_ArbitraryFragmentNotCommitted(t *testing.T) {
	at, port, _ := newScriptedAT(t, 200*time.Millisecond)
	port.enqueue(chunk("+CPIN: RE")) // fragment, never completed

	_, err := at.Command("AT+CPIN?")
	if !errors.Is(err, ErrModemTimeout) {
		t.Fatalf("error = %v, want timeout (fragment must not become a line)", err)
	}
}

// Echo of the command itself is skipped even before ATE0 takes effect.
func TestSimpleAT_EchoSkipped(t *testing.T) {
	at, port, _ := newScriptedAT(t, time.Second)
	port.enqueue(chunk("AT+CSQ\r\n"), chunk("+CSQ: 20,0\r\n"), chunk("OK\r\n"))

	lines, err := at.Command("AT+CSQ")
	if err != nil {
		t.Fatalf("Command() error = %v", err)
	}
	if len(lines) != 1 || !strings.HasPrefix(lines[0], "+CSQ:") {
		t.Errorf("lines = %v, want only +CSQ payload", lines)
	}
}

func TestInitModemSession_HappyPath(t *testing.T) {
	at := newFakeAT()
	at.on("AT+CMGF?", []string{"+CMGF: 0"}, nil)
	at.on(`AT+CPMS="SM","SM","SM"`, []string{`+CPMS: 3,30,3,30,3,30`}, nil)

	used, total, err := initModemSession(at)
	if err != nil {
		t.Fatalf("initModemSession() error = %v", err)
	}
	if used != 3 || total != 30 {
		t.Errorf("capacity = %d/%d, want 3/30", used, total)
	}
	for _, cmd := range []string{"ATE0", "AT+CMGF=0", "AT+CNMI=2,0,0,0,0"} {
		if at.commandCount(cmd) != 1 {
			t.Errorf("command %s issued %d times, want 1", cmd, at.commandCount(cmd))
		}
	}
}

func TestInitModemSession_TextModeStuck(t *testing.T) {
	at := newFakeAT()
	at.on("AT+CMGF?", []string{"+CMGF: 1"}, nil) // modem kept text mode

	_, _, err := initModemSession(at)
	var diagErr *DiagnosticError
	if !errors.As(err, &diagErr) || diagErr.Type != ErrTypeModemInitFailed {
		t.Fatalf("error = %v, want ErrTypeModemInitFailed", err)
	}
}

func TestInitModemSession_CNMIFallback(t *testing.T) {
	at := newFakeAT()
	at.on("AT+CMGF?", []string{"+CMGF: 0"}, nil)
	at.on("AT+CNMI=2,0,0,0,0", nil, ErrModemError)

	if _, _, err := initModemSession(at); err != nil {
		t.Fatalf("initModemSession() error = %v (fallback CNMI should succeed)", err)
	}
	if at.commandCount("AT+CNMI=0,0,0,0,0") != 1 {
		t.Error("fallback AT+CNMI=0,0,0,0,0 was not attempted")
	}
}

func TestInitModemSession_TransportErrorIsSessionError(t *testing.T) {
	at := newFakeAT()
	at.on("AT", nil, ErrModemTimeout)

	_, _, err := initModemSession(at)
	var sessErr *SessionError
	if !errors.As(err, &sessErr) {
		t.Fatalf("error = %v, want SessionError", err)
	}
}

// A mandatory init command failing with a modem ERROR while the SIM is absent
// must be reported as SIM Not Detected (not Modem Init Failed), so the whole
// SIM-out episode keeps one error type (dedup → single alert) and inherits the
// SIM reset-and-recover path. Regression for the live SIM-pull test.
func TestInitModemSession_CMGFErrorWithSIMOut(t *testing.T) {
	at := newFakeAT()
	at.on("AT+CMGF=0", nil, ErrModemError)
	at.on("AT+CPIN?", nil, ErrModemError) // SIM gone: CPIN also errors

	_, _, err := initModemSession(at)
	var diagErr *DiagnosticError
	if !errors.As(err, &diagErr) || diagErr.Type != ErrTypeSimNotDetected {
		t.Fatalf("error = %v, want ErrTypeSimNotDetected", err)
	}
}

// The same failure with the SIM actually READY is a genuine init problem and
// stays Modem Init Failed.
func TestInitModemSession_CMGFErrorWithSIMReady(t *testing.T) {
	at := newFakeAT()
	at.on("AT+CMGF=0", nil, ErrModemError)
	at.on("AT+CPIN?", []string{"+CPIN: READY"}, nil)

	_, _, err := initModemSession(at)
	var diagErr *DiagnosticError
	if !errors.As(err, &diagErr) || diagErr.Type != ErrTypeModemInitFailed {
		t.Fatalf("error = %v, want ErrTypeModemInitFailed", err)
	}
}

// A transport timeout during the SIM reprobe stays a SessionError (quiet reopen).
func TestInitModemSession_CMGFErrorProbeTimeout(t *testing.T) {
	at := newFakeAT()
	at.on("AT+CMGF=0", nil, ErrModemError)
	at.on("AT+CPIN?", nil, ErrModemTimeout)

	_, _, err := initModemSession(at)
	var sessErr *SessionError
	if !errors.As(err, &sessErr) {
		t.Fatalf("error = %v, want SessionError", err)
	}
}

// CommandWithPrompt happy path: prompt, payload with Ctrl+Z, +CMGS result.
func TestSimpleAT_CommandWithPrompt(t *testing.T) {
	at, port, _ := newScriptedAT(t, 5*time.Second)
	port.enqueue(
		chunk("\r\n> "),
		eofChunk(), // modem waits for the payload
		chunk("+CMGS: 42\r\n"),
		chunk("OK\r\n"),
	)

	lines, err := at.CommandWithPrompt("AT+CMGS=28", "0001000B915348948470870000", 5*time.Second)
	if err != nil {
		t.Fatalf("CommandWithPrompt() error = %v", err)
	}
	if len(lines) != 1 || !strings.HasPrefix(lines[0], "+CMGS:") {
		t.Fatalf("lines = %v, want +CMGS result", lines)
	}
	if len(port.writes) != 2 {
		t.Fatalf("writes = %d, want 2 (command, then payload)", len(port.writes))
	}
	if !strings.HasSuffix(port.writes[1], "\x1a") {
		t.Error("payload must be terminated with Ctrl+Z")
	}
	if at.Poisoned() {
		t.Error("successful prompt dialog must not poison the session")
	}
}

// A terminal error instead of the prompt: the command fails cleanly, the
// payload is never written, the session stays synchronized.
func TestSimpleAT_CommandWithPrompt_RejectedBeforePrompt(t *testing.T) {
	at, port, _ := newScriptedAT(t, 5*time.Second)
	port.enqueue(chunk("+CMS ERROR: 302\r\n"))

	_, err := at.CommandWithPrompt("AT+CMGS=28", "00", 5*time.Second)
	if !IsModemError(err) {
		t.Fatalf("error = %v, want modem error", err)
	}
	if len(port.writes) != 1 {
		t.Errorf("writes = %d, want 1 (payload must not be sent)", len(port.writes))
	}
	if at.Poisoned() {
		t.Error("clean rejection must not poison the session")
	}
}

// No prompt at all: deadline poisons the session (payload never sent).
func TestSimpleAT_CommandWithPrompt_NoPromptPoisons(t *testing.T) {
	at, port, _ := newScriptedAT(t, 200*time.Millisecond)

	_, err := at.CommandWithPrompt("AT+CMGS=28", "00", 200*time.Millisecond)
	if !errors.Is(err, ErrModemTimeout) {
		t.Fatalf("error = %v, want timeout", err)
	}
	if !at.Poisoned() {
		t.Error("missing prompt must poison the session")
	}
	if len(port.writes) != 1 {
		t.Errorf("writes = %d, want 1", len(port.writes))
	}
}
