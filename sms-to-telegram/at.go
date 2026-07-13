// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"strings"
	"time"
)

// Specific error types for better error handling
var (
	ErrModemTimeout    = errors.New("modem timeout: no response received")
	ErrModemError      = errors.New("modem returned ERROR")
	ErrModemDisconnect = errors.New("modem disconnected or not responding")
	ErrWriteFailed     = errors.New("failed to write to modem")
	// ErrSessionPoisoned marks a session whose response stream can no longer be
	// trusted (deadline hit mid-response, transport failure, framing loss).
	// Every later command is rejected; the caller must reopen the port.
	ErrSessionPoisoned = errors.New("modem session poisoned: reopen required")
)

// idleReadDelay paces the read loop when the port reports no data. A real
// serial read already blocks for the VTIME interval; the extra sleep keeps a
// tight EOF loop (mock port, dead file descriptor) from spinning and gives the
// fake clock in tests something to advance.
const idleReadDelay = 50 * time.Millisecond

// Unsolicited result codes (URCs) the modem may interleave with command
// responses. They are never part of a solicited response and are skipped.
// Values give the number of extra payload lines that follow the URC line.
var urcPrefixes = map[string]int{
	"+CMTI:":            0, // new SMS stored at index
	"+CDSI:":            0, // status report stored at index
	"+CLIP:":            0,
	"+CCWA:":            0,
	"+CMT:":             1, // SMS delivered directly: header line + PDU line
	"+CDS:":             1, // status report delivered directly
	"+CBM:":             1, // cell broadcast
	"RING":              0,
	"NO CARRIER":        0,
	"BUSY":              0,
	"NO DIALTONE":       0,
	"NO ANSWER":         0,
	"RDY":               0, // SIM800 boot banner
	"Call Ready":        0,
	"SMS Ready":         0,
	"NORMAL POWER DOWN": 0,
	"UNDER-VOLTAGE":     0, // SIM800 "UNDER-VOLTAGE POWER DOWN"/"WARNNING"
	"OVER-VOLTAGE":      0,
}

// classifyURC returns (extraPayloadLines, true) when the line is a known URC.
func classifyURC(line string) (int, bool) {
	for prefix, payload := range urcPrefixes {
		if strings.HasPrefix(line, prefix) {
			return payload, true
		}
	}
	return 0, false
}

// SimpleAT is a synchronous AT command session over a serial port.
//
// It owns one persistent buffered reader and a partial-line accumulator, so a
// response split across VTIME read timeouts is reassembled instead of being
// committed as two bogus lines. A command either consumes its complete
// terminal result (OK / ERROR / +CME ERROR / +CMS ERROR) or poisons the
// session: after a deadline or transport failure the stream may hold a late
// response that would satisfy the wrong command, so every later command fails
// with ErrSessionPoisoned until the caller reopens the port.
//
// Not safe for concurrent use; the whole application talks to the modem from
// a single goroutine.
type SimpleAT struct {
	port     io.ReadWriter
	reader   *bufio.Reader
	timeout  time.Duration
	partial  string
	poisoned bool
}

// NewSimpleAT creates a new AT command session for an opened port.
func NewSimpleAT(port io.ReadWriter, timeout time.Duration) *SimpleAT {
	return &SimpleAT{
		port:    port,
		reader:  bufio.NewReader(port),
		timeout: timeout,
	}
}

// Poisoned reports whether the session must be reopened.
func (s *SimpleAT) Poisoned() bool { return s.poisoned }

// terminalFragment reports whether a partial line without a trailing newline
// is a complete terminal result on its own. Some modems omit the final
// newline after OK/ERROR; accepting exactly these fragments is deliberate —
// arbitrary fragments are never committed.
func terminalFragment(line string) bool {
	return line == "OK" || line == "ERROR" ||
		strings.HasPrefix(line, "+CME ERROR:") || strings.HasPrefix(line, "+CMS ERROR:")
}

var errReadDeadline = errors.New("read deadline exceeded")

// readLine returns the next complete line (without CR/LF), reassembling
// fragments across idle read timeouts. It returns errReadDeadline when the
// deadline passes, and ErrModemDisconnect on a hard transport error.
func (s *SimpleAT) readLine(deadline time.Time) (string, error) {
	for {
		// A complete line may already be buffered.
		if idx := strings.IndexByte(s.partial, '\n'); idx >= 0 {
			line := strings.TrimSpace(s.partial[:idx])
			s.partial = s.partial[idx+1:]
			return line, nil
		}

		if !clk.Now().Before(deadline) {
			// Deliberate exception: a dangling OK/ERROR fragment is a valid
			// terminal even without its newline.
			if frag := strings.TrimSpace(s.partial); terminalFragment(frag) {
				s.partial = ""
				return frag, nil
			}
			return "", errReadDeadline
		}

		data, err := s.reader.ReadString('\n')
		s.partial += data
		if err == nil {
			continue // got a newline; loop extracts the line
		}
		if err == io.EOF || err == io.ErrNoProgress {
			// VTIME expired with no (or partial) data. If the fragment is a
			// bare terminal, accept it once the port went idle: nothing more
			// is coming for this line.
			if frag := strings.TrimSpace(s.partial); terminalFragment(frag) {
				s.partial = ""
				return frag, nil
			}
			clk.Sleep(idleReadDelay)
			continue
		}
		// Hard transport error: the port is gone.
		s.poisoned = true
		return "", fmt.Errorf("%w: %v", ErrModemDisconnect, err)
	}
}

// Command sends an AT command and returns the response lines
// (excluding echo, URCs and the terminal OK).
func (s *SimpleAT) Command(cmd string) ([]string, error) {
	return s.CommandWithTimeout(cmd, s.timeout)
}

// CommandWithTimeout sends an AT command with a custom timeout.
func (s *SimpleAT) CommandWithTimeout(cmd string, timeout time.Duration) ([]string, error) {
	if s.poisoned {
		return nil, ErrSessionPoisoned
	}

	if _, err := s.port.Write([]byte(cmd + "\r\n")); err != nil {
		s.poisoned = true
		return nil, fmt.Errorf("%w: %v", ErrWriteFailed, err)
	}

	return s.collectResponse(cmd, clk.Now().Add(timeout))
}

// collectResponse reads response lines until a terminal result (OK / ERROR /
// +CME ERROR / +CMS ERROR), skipping echo of `echo` and URCs. On deadline the
// session is poisoned.
func (s *SimpleAT) collectResponse(echo string, deadline time.Time) ([]string, error) {
	var lines []string
	urcPayloadLeft := 0

	for {
		line, err := s.readLine(deadline)
		if err != nil {
			if errors.Is(err, ErrModemDisconnect) {
				return nil, err
			}
			// Deadline: a late response may still arrive and desynchronize
			// the stream; the session cannot be trusted anymore.
			s.poisoned = true
			if len(lines) == 0 && s.partial == "" {
				return nil, ErrModemTimeout
			}
			return nil, fmt.Errorf("%w: incomplete response (%d lines)", ErrModemTimeout, len(lines))
		}

		if line == "" {
			continue
		}

		// Payload line(s) of a multi-line URC (e.g. the PDU after +CMT:).
		if urcPayloadLeft > 0 {
			urcPayloadLeft--
			slog.Debug("Skipping URC payload line during command", "cmd", echo)
			continue
		}

		// Echo (before ATE0 takes effect).
		if line == echo {
			continue
		}

		if payload, isURC := classifyURC(line); isURC {
			slog.Debug("Skipping URC during command", "cmd", echo, "urc", line)
			urcPayloadLeft = payload
			continue
		}

		switch {
		case line == "OK":
			return lines, nil
		case line == "ERROR":
			return nil, ErrModemError
		case strings.HasPrefix(line, "+CME ERROR:") || strings.HasPrefix(line, "+CMS ERROR:"):
			// Extended error codes are terminal result lines: the response is
			// complete and the session stays synchronized.
			return nil, fmt.Errorf("%w: %s", ErrModemError, line)
		}

		lines = append(lines, line)
	}
}

// CommandWithPrompt drives the two-phase prompt dialog used by AT+CMGS (and
// similar commands): it sends cmd, waits for the "> " prompt, writes payload
// terminated by Ctrl+Z, and collects the final response. Used by the live
// loopback test suite to send SMS through the modem; production forwarding
// never sends SMS.
func (s *SimpleAT) CommandWithPrompt(cmd, payload string, timeout time.Duration) ([]string, error) {
	if s.poisoned {
		return nil, ErrSessionPoisoned
	}

	if _, err := s.port.Write([]byte(cmd + "\r\n")); err != nil {
		s.poisoned = true
		return nil, fmt.Errorf("%w: %v", ErrWriteFailed, err)
	}

	deadline := clk.Now().Add(timeout)

	// Phase 1: wait for the prompt. The "> " prompt has no trailing newline,
	// so it is detected in the partial buffer once the port goes idle.
	// Complete lines arriving first are echo/URCs (skipped) or a terminal
	// rejection (the modem refused to open the prompt).
	for {
		if idx := strings.IndexByte(s.partial, '\n'); idx >= 0 {
			line := strings.TrimSpace(s.partial[:idx])
			s.partial = s.partial[idx+1:]
			switch {
			case line == "" || line == cmd:
			case line == "ERROR":
				return nil, ErrModemError
			case strings.HasPrefix(line, "+CME ERROR:") || strings.HasPrefix(line, "+CMS ERROR:"):
				return nil, fmt.Errorf("%w: %s", ErrModemError, line)
			default:
				// URC or noise while waiting for the prompt.
				slog.Debug("Skipping line while waiting for prompt", "cmd", cmd, "line", line)
			}
			continue
		}

		if strings.HasPrefix(strings.TrimSpace(s.partial), ">") {
			s.partial = ""
			break
		}

		if !clk.Now().Before(deadline) {
			s.poisoned = true
			return nil, fmt.Errorf("%w: no prompt for %s", ErrModemTimeout, cmd)
		}

		data, err := s.reader.ReadString('\n')
		s.partial += data
		if err == nil || err == io.EOF || err == io.ErrNoProgress {
			if err != nil && !strings.HasPrefix(strings.TrimSpace(s.partial), ">") {
				clk.Sleep(idleReadDelay)
			}
			continue
		}
		s.poisoned = true
		return nil, fmt.Errorf("%w: %v", ErrModemDisconnect, err)
	}

	// Phase 2: send the payload terminated by Ctrl+Z and read the result
	// (network submission may take many seconds; the shared deadline governs).
	if _, err := s.port.Write([]byte(payload + "\x1a")); err != nil {
		s.poisoned = true
		return nil, fmt.Errorf("%w: %v", ErrWriteFailed, err)
	}

	return s.collectResponse(payload, deadline)
}

// Ping sends a simple AT command to check if modem is responsive.
func (s *SimpleAT) Ping() error {
	_, err := s.CommandWithTimeout("AT", 2*time.Second)
	return err
}

// IsTimeoutError checks if an error is a timeout/transport-related error that
// invalidates the current session.
func IsTimeoutError(err error) bool {
	return errors.Is(err, ErrModemTimeout) ||
		errors.Is(err, ErrModemDisconnect) ||
		errors.Is(err, ErrSessionPoisoned) ||
		errors.Is(err, ErrWriteFailed)
}

// IsModemError checks if an error is a modem ERROR response (the modem is
// alive and the session is still synchronized).
func IsModemError(err error) bool {
	return errors.Is(err, ErrModemError)
}
