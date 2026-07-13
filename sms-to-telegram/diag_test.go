// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

// diagAT builds a fakeAT preloaded with a healthy baseline; individual tests
// override the interesting command.
func diagAT() *fakeAT {
	at := newFakeAT()
	at.on("AT+CPIN?", []string{"+CPIN: READY"}, nil)
	at.on("AT+CSQ", []string{"+CSQ: 20,0"}, nil)
	at.on("AT+CREG?", []string{"+CREG: 0,1"}, nil)
	return at
}

func runDiag(t *testing.T, at *fakeAT) error {
	t.Helper()
	fc := newFakeClock()
	t.Cleanup(swapClock(fc))
	return runModemDiagnostics(context.Background(), at, fc.Now(), 90*time.Second)
}

func wantDiagType(t *testing.T, err error, wantType DiagnosticErrorType) {
	t.Helper()
	var diagErr *DiagnosticError
	if !errors.As(err, &diagErr) {
		t.Fatalf("error = %v, want DiagnosticError", err)
	}
	if diagErr.Type != wantType {
		t.Fatalf("error type = %s, want %s (message: %s)",
			errorTypeName(diagErr.Type), errorTypeName(wantType), diagErr.Message)
	}
}

func TestDiagnostics_Healthy(t *testing.T) {
	if err := runDiag(t, diagAT()); err != nil {
		t.Fatalf("diagnostics error = %v, want nil", err)
	}
}

// Regression: "+CPIN: NOT READY" contains the substring "READY" and used to
// be classified as a ready SIM.
func TestDiagnostics_NotReadyIsNotReady(t *testing.T) {
	at := diagAT()
	at.responses["AT+CPIN?"] = nil
	at.on("AT+CPIN?", []string{"+CPIN: NOT READY"}, nil)

	err := runDiag(t, at)
	wantDiagType(t, err, ErrTypeSimNotDetected)
	if !strings.Contains(err.Error(), "not ready") {
		t.Errorf("message = %q, want a not-ready explanation", err.Error())
	}
}

func TestDiagnostics_PinRequired(t *testing.T) {
	at := diagAT()
	at.responses["AT+CPIN?"] = nil
	at.on("AT+CPIN?", []string{"+CPIN: SIM PIN"}, nil)
	wantDiagType(t, runDiag(t, at), ErrTypeSimPinRequired)
}

func TestDiagnostics_RegistrationDenied(t *testing.T) {
	at := diagAT()
	at.responses["AT+CREG?"] = nil
	at.on("AT+CREG?", []string{"+CREG: 0,3"}, nil)
	wantDiagType(t, runDiag(t, at), ErrTypeNetworkDenied)
}

// CSQ=99 within the grace window is tolerated; only after the grace expires
// does it become a No Signal alert.
func TestDiagnostics_NoSignalAfterGrace(t *testing.T) {
	at := diagAT()
	at.responses["AT+CSQ"] = nil
	at.on("AT+CSQ", []string{"+CSQ: 99,99"}, nil)
	wantDiagType(t, runDiag(t, at), ErrTypeNoSignal)
}

func TestDiagnostics_SignalAppearsWithinGrace(t *testing.T) {
	at := diagAT()
	at.responses["AT+CSQ"] = nil
	at.on("AT+CSQ", []string{"+CSQ: 99,99"}, nil)
	at.on("AT+CSQ", []string{"+CSQ: 99,99"}, nil)
	at.on("AT+CSQ", []string{"+CSQ: 21,0"}, nil)

	if err := runDiag(t, at); err != nil {
		t.Fatalf("diagnostics error = %v, want recovery within grace", err)
	}
}

func TestDiagnostics_SearchingThenRegistered(t *testing.T) {
	at := diagAT()
	at.responses["AT+CREG?"] = nil
	at.on("AT+CREG?", []string{"+CREG: 0,2"}, nil)
	at.on("AT+CREG?", []string{"+CREG: 0,1"}, nil)

	if err := runDiag(t, at); err != nil {
		t.Fatalf("diagnostics error = %v, want success after registration", err)
	}
}

func TestDiagnostics_NotRegisteredAfterGrace(t *testing.T) {
	at := diagAT()
	at.responses["AT+CREG?"] = nil
	at.on("AT+CREG?", []string{"+CREG: 0,2"}, nil)
	wantDiagType(t, runDiag(t, at), ErrTypeNetworkNotRegistered)
}

// A transport failure during diagnostics is a session problem, not a SIM
// problem — it must not trigger a misleading SIM alert or a CFUN reset.
func TestDiagnostics_TransportErrorIsSessionError(t *testing.T) {
	at := diagAT()
	at.responses["AT+CPIN?"] = nil
	at.on("AT+CPIN?", nil, ErrModemTimeout)

	err := runDiag(t, at)
	var sessErr *SessionError
	if !errors.As(err, &sessErr) {
		t.Fatalf("error = %v, want SessionError", err)
	}
}

func TestDiagnostics_SimErrorWithCCIDPresent(t *testing.T) {
	at := diagAT()
	at.responses["AT+CPIN?"] = nil
	at.on("AT+CPIN?", nil, ErrModemError) // repeats for all 5 attempts
	at.on("AT+CCID", []string{"898600810906F8048812"}, nil)

	err := runDiag(t, at)
	wantDiagType(t, err, ErrTypeSimNotDetected)
	if !strings.Contains(err.Error(), "detected but not ready") {
		t.Errorf("message = %q, want detected-but-not-ready", err.Error())
	}
	if at.commandCount("AT+CPIN?") != 5 {
		t.Errorf("CPIN attempts = %d, want 5", at.commandCount("AT+CPIN?"))
	}
}

func TestDiagnostics_CancelDuringGrace(t *testing.T) {
	fc := newFakeClock()
	t.Cleanup(swapClock(fc))
	at := diagAT()
	at.responses["AT+CREG?"] = nil
	at.on("AT+CREG?", []string{"+CREG: 0,2"}, nil)

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := runModemDiagnostics(ctx, at, fc.Now(), 90*time.Second)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context.Canceled (no alert on shutdown)", err)
	}
}

// --- Notifier -----------------------------------------------------------------

// A chat that missed an alert is retried while the successfully notified chat
// is not spammed again.
func TestErrorNotifier_PerChatRetry(t *testing.T) {
	sender := &fakeSender{}
	failB := true
	sender.script = func(_ int, chatID int64, _ string) error {
		if chatID == 200 && failB {
			return errors.New("network down")
		}
		return nil
	}
	notifier := NewErrorNotifier(sender, []int64{100, 200}, false, "host", time.Second)
	diagErr := NewDiagnosticError(ErrTypeNoSignal, "no signal")

	if !notifier.NotifyError(context.Background(), diagErr) {
		t.Fatal("first notification should reach at least chat 100")
	}
	if len(sender.sentTo(100)) != 1 || len(sender.sentTo(200)) != 1 {
		t.Fatalf("attempts: chat100=%d chat200=%d, want 1/1", len(sender.sentTo(100)), len(sender.sentTo(200)))
	}

	// Retry: only the failed chat is re-attempted.
	failB = false
	if !notifier.NotifyError(context.Background(), diagErr) {
		t.Fatal("retry should deliver to chat 200")
	}
	if len(sender.sentTo(100)) != 1 {
		t.Errorf("chat 100 re-notified (%d sends)", len(sender.sentTo(100)))
	}
	if len(sender.sentTo(200)) != 2 {
		t.Errorf("chat 200 attempts = %d, want 2", len(sender.sentTo(200)))
	}

	// Fully delivered: nothing more to send.
	if notifier.NotifyError(context.Background(), diagErr) {
		t.Error("duplicate notification after full delivery")
	}
}

func TestErrorNotifier_RecoveryOnlyForNotifiedChats(t *testing.T) {
	sender := &fakeSender{}
	failB := true
	sender.script = func(_ int, chatID int64, _ string) error {
		if chatID == 200 && failB {
			return errors.New("down")
		}
		return nil
	}
	notifier := NewErrorNotifier(sender, []int64{100, 200}, false, "host", time.Second)
	notifier.NotifyError(context.Background(), NewDiagnosticError(ErrTypeNoSignal, "x"))

	failB = false
	if !notifier.NotifyRecovery(context.Background()) {
		t.Fatal("recovery should be sent to chat 100")
	}
	// Chat 200 never learned about the error, so it gets no recovery.
	if len(sender.sentTo(200)) != 1 { // only the failed error attempt
		t.Errorf("chat 200 messages = %d, want 1 (no recovery for unnotified chat)", len(sender.sentTo(200)))
	}
	if notifier.HasError() {
		t.Error("HasError should be false after recovery")
	}
}

// Raw modem garbage in the diagnostic message must not break Telegram's HTML
// parser — otherwise the alert itself fails exactly when it is needed.
func TestErrorNotifier_EscapesHostileMessage(t *testing.T) {
	notifier := NewErrorNotifier(nil, []int64{1}, true, "host<&>", time.Second)
	diagErr := NewDiagnosticError(ErrTypeSimNotDetected, "Unknown SIM status: <garbage & more>")

	msg := notifier.formatErrorMessage(diagErr)
	if strings.Contains(msg, "<garbage") {
		t.Error("raw modem output not escaped")
	}
	if !strings.Contains(msg, "&lt;garbage &amp; more&gt;") {
		t.Errorf("escaped payload missing: %q", msg)
	}
	if !strings.Contains(msg, "host&lt;&amp;&gt;") {
		t.Errorf("hostname not escaped: %q", msg)
	}
}

func TestCheckStorage_Hysteresis(t *testing.T) {
	sender := &fakeSender{}
	notifier := NewErrorNotifier(sender, []int64{100}, false, "host", time.Second)
	ctx := context.Background()

	notifier.CheckStorage(ctx, 25, 30) // 83% → alert
	if len(sender.sent) != 1 {
		t.Fatalf("alerts = %d, want 1", len(sender.sent))
	}
	notifier.CheckStorage(ctx, 26, 30) // still high → no repeat
	if len(sender.sent) != 1 {
		t.Fatalf("alerts = %d, want still 1", len(sender.sent))
	}
	notifier.CheckStorage(ctx, 20, 30) // 66% → clears
	notifier.CheckStorage(ctx, 25, 30) // high again → re-alert
	if len(sender.sent) != 2 {
		t.Fatalf("alerts = %d, want 2 after hysteresis reset", len(sender.sent))
	}
	notifier.CheckStorage(ctx, -1, 0) // unknown capacity → ignored
	if len(sender.sent) != 2 {
		t.Fatalf("alerts = %d, want 2", len(sender.sent))
	}
}

func TestNeedsModemReset(t *testing.T) {
	reset := []DiagnosticErrorType{
		ErrTypeSimNotDetected, ErrTypeSimPinRequired, ErrTypeSimPukLocked,
		ErrTypeNetworkDenied, ErrTypeModemInitFailed,
	}
	noReset := []DiagnosticErrorType{
		ErrTypeNone, ErrTypeSerialPort, ErrTypeModemNotResponding,
		ErrTypeNetworkNotRegistered, ErrTypeNoSignal, ErrTypeStorageLow,
		ErrTypeDeliveryRejected,
	}
	for _, tp := range reset {
		if !needsModemReset(tp) {
			t.Errorf("needsModemReset(%s) = false, want true", errorTypeName(tp))
		}
	}
	for _, tp := range noReset {
		if needsModemReset(tp) {
			t.Errorf("needsModemReset(%s) = true, want false", errorTypeName(tp))
		}
	}
}
