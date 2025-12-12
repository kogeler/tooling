package main

import (
	"bytes"
	"errors"
	"io"
	"testing"
	"time"
)

// mockPort implements io.ReadWriter for testing
type mockPort struct {
	readData  []byte
	readPos   int
	writeData bytes.Buffer
	readDelay time.Duration
	readErr   error
}

func newMockPort(response string) *mockPort {
	return &mockPort{
		readData: []byte(response),
	}
}

func (m *mockPort) Read(p []byte) (n int, err error) {
	if m.readDelay > 0 {
		time.Sleep(m.readDelay)
	}
	if m.readErr != nil {
		return 0, m.readErr
	}
	if m.readPos >= len(m.readData) {
		return 0, io.EOF
	}
	n = copy(p, m.readData[m.readPos:])
	m.readPos += n
	return n, nil
}

func (m *mockPort) Write(p []byte) (n int, err error) {
	return m.writeData.Write(p)
}

func TestSimpleAT_Command_OK(t *testing.T) {
	port := newMockPort("AT\r\nOK\r\n")
	at := NewSimpleAT(port, 1*time.Second)

	lines, err := at.Command("AT")
	if err != nil {
		t.Fatalf("Command() error = %v", err)
	}
	if len(lines) != 0 {
		t.Errorf("Expected 0 lines, got %d", len(lines))
	}
}

func TestSimpleAT_Command_WithResponse(t *testing.T) {
	port := newMockPort("AT+CPIN?\r\n+CPIN: READY\r\nOK\r\n")
	at := NewSimpleAT(port, 1*time.Second)

	lines, err := at.Command("AT+CPIN?")
	if err != nil {
		t.Fatalf("Command() error = %v", err)
	}
	if len(lines) != 1 {
		t.Fatalf("Expected 1 line, got %d", len(lines))
	}
	if lines[0] != "+CPIN: READY" {
		t.Errorf("Expected '+CPIN: READY', got %q", lines[0])
	}
}

func TestSimpleAT_Command_MultipleLines(t *testing.T) {
	response := "ATI\r\nSIM800 R14.18\r\nManufacturer: SIMCOM\r\nOK\r\n"
	port := newMockPort(response)
	at := NewSimpleAT(port, 1*time.Second)

	lines, err := at.Command("ATI")
	if err != nil {
		t.Fatalf("Command() error = %v", err)
	}
	if len(lines) != 2 {
		t.Fatalf("Expected 2 lines, got %d: %v", len(lines), lines)
	}
}

func TestSimpleAT_Command_ERROR(t *testing.T) {
	port := newMockPort("AT+INVALID\r\nERROR\r\n")
	at := NewSimpleAT(port, 1*time.Second)

	_, err := at.Command("AT+INVALID")
	if err == nil {
		t.Fatal("Expected error, got nil")
	}
	if !IsModemError(err) {
		t.Errorf("Expected modem error, got: %v", err)
	}
}

func TestSimpleAT_Command_CMEError(t *testing.T) {
	port := newMockPort("AT+CPIN?\r\n+CME ERROR: SIM not inserted\r\n")
	at := NewSimpleAT(port, 1*time.Second)

	_, err := at.Command("AT+CPIN?")
	if err == nil {
		t.Fatal("Expected error, got nil")
	}
	if !IsModemError(err) {
		t.Errorf("Expected modem error, got: %v", err)
	}
}

func TestSimpleAT_Command_CMSError(t *testing.T) {
	port := newMockPort("AT+CMGL=4\r\n+CMS ERROR: 500\r\n")
	at := NewSimpleAT(port, 1*time.Second)

	_, err := at.Command("AT+CMGL=4")
	if err == nil {
		t.Fatal("Expected error, got nil")
	}
	if !IsModemError(err) {
		t.Errorf("Expected modem error, got: %v", err)
	}
}

func TestSimpleAT_Command_Timeout(t *testing.T) {
	port := &mockPort{
		readData:  []byte{},
		readDelay: 50 * time.Millisecond,
	}
	at := NewSimpleAT(port, 100*time.Millisecond)

	_, err := at.Command("AT")
	if err == nil {
		t.Fatal("Expected timeout error")
	}
	if !IsTimeoutError(err) {
		t.Errorf("Expected timeout error, got: %v", err)
	}
}

func TestSimpleAT_Ping(t *testing.T) {
	port := newMockPort("AT\r\nOK\r\n")
	at := NewSimpleAT(port, 1*time.Second)

	err := at.Ping()
	if err != nil {
		t.Errorf("Ping() error = %v", err)
	}
}

func TestSimpleAT_Ping_Fail(t *testing.T) {
	port := &mockPort{
		readErr: io.EOF,
	}
	at := NewSimpleAT(port, 100*time.Millisecond)

	err := at.Ping()
	if err == nil {
		t.Error("Expected Ping() to fail")
	}
}

func TestIsTimeoutError(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want bool
	}{
		{"timeout error", ErrModemTimeout, true},
		{"disconnect error", ErrModemDisconnect, true},
		{"modem error", ErrModemError, false},
		{"write error", ErrWriteFailed, false},
		{"nil error", nil, false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := IsTimeoutError(tt.err); got != tt.want {
				t.Errorf("IsTimeoutError() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestIsModemError(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want bool
	}{
		{"modem error", ErrModemError, true},
		{"timeout error", ErrModemTimeout, false},
		{"disconnect error", ErrModemDisconnect, false},
		{"nil error", nil, false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := IsModemError(tt.err); got != tt.want {
				t.Errorf("IsModemError() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestErrorsAre(t *testing.T) {
	// Test that wrapped errors still match
	wrapped := errors.Join(ErrModemError, errors.New("additional context"))

	if !errors.Is(wrapped, ErrModemError) {
		t.Error("Wrapped error should match ErrModemError")
	}
}

func TestSimpleAT_CommandWithTimeout(t *testing.T) {
	port := newMockPort("AT\r\nOK\r\n")
	at := NewSimpleAT(port, 5*time.Second)

	// Use shorter timeout
	lines, err := at.CommandWithTimeout("AT", 1*time.Second)
	if err != nil {
		t.Fatalf("CommandWithTimeout() error = %v", err)
	}
	if len(lines) != 0 {
		t.Errorf("Expected 0 lines, got %d", len(lines))
	}
}

func TestSimpleAT_WritesCorrectCommand(t *testing.T) {
	port := newMockPort("AT+CMGF=0\r\nOK\r\n")
	at := NewSimpleAT(port, 1*time.Second)

	at.Command("AT+CMGF=0")

	written := port.writeData.String()
	expected := "AT+CMGF=0\r\n"
	if written != expected {
		t.Errorf("Written = %q, want %q", written, expected)
	}
}
