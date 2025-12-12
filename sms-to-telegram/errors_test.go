package main

import (
	"context"
	"testing"
)

func TestDiagnosticError(t *testing.T) {
	err := NewDiagnosticError(ErrTypeModemNotResponding, "test error: %s", "details")

	if err.Type != ErrTypeModemNotResponding {
		t.Errorf("Type = %v, want %v", err.Type, ErrTypeModemNotResponding)
	}

	if err.Message != "test error: details" {
		t.Errorf("Message = %q, want %q", err.Message, "test error: details")
	}

	// Test Error() interface
	if err.Error() != "test error: details" {
		t.Errorf("Error() = %q, want %q", err.Error(), "test error: details")
	}
}

func TestErrorNotifier_Deduplication(t *testing.T) {
	ctx := context.Background()
	notifier := NewErrorNotifier(nil, []int64{123}, true, "test-host")

	err1 := NewDiagnosticError(ErrTypeModemNotResponding, "modem error")
	err2 := NewDiagnosticError(ErrTypeModemNotResponding, "modem error again")
	err3 := NewDiagnosticError(ErrTypeSimNotDetected, "sim error")

	// First notification should be sent
	if !notifier.NotifyError(ctx, err1) {
		t.Error("First notification should be sent")
	}

	// Same error type - should NOT be sent
	if notifier.NotifyError(ctx, err2) {
		t.Error("Duplicate error type should not be sent")
	}

	// Different error type - should be sent
	if !notifier.NotifyError(ctx, err3) {
		t.Error("Different error type should be sent")
	}

	// Same as last (sim error) - should NOT be sent
	if notifier.NotifyError(ctx, err3) {
		t.Error("Duplicate error type should not be sent")
	}
}

func TestErrorNotifier_Recovery(t *testing.T) {
	ctx := context.Background()
	notifier := NewErrorNotifier(nil, []int64{123}, true, "test-host")

	// No previous error - recovery should not be sent
	if notifier.NotifyRecovery(ctx) {
		t.Error("Recovery should not be sent when no previous error")
	}

	// Set an error
	err := NewDiagnosticError(ErrTypeSerialPort, "port error")
	notifier.NotifyError(ctx, err)

	// Now recovery should be sent
	if !notifier.NotifyRecovery(ctx) {
		t.Error("Recovery should be sent after error")
	}

	// Recovery again - should not be sent (already recovered)
	if notifier.NotifyRecovery(ctx) {
		t.Error("Recovery should not be sent twice")
	}
}

func TestErrorNotifier_HasError(t *testing.T) {
	ctx := context.Background()
	notifier := NewErrorNotifier(nil, []int64{123}, true, "test-host")

	if notifier.HasError() {
		t.Error("HasError should be false initially")
	}

	err := NewDiagnosticError(ErrTypeSimNotDetected, "sim error")
	notifier.NotifyError(ctx, err)

	if !notifier.HasError() {
		t.Error("HasError should be true after error")
	}

	notifier.NotifyRecovery(ctx)

	if notifier.HasError() {
		t.Error("HasError should be false after recovery")
	}
}

func TestErrorNotifier_ErrorAfterRecovery(t *testing.T) {
	ctx := context.Background()
	notifier := NewErrorNotifier(nil, []int64{123}, true, "test-host")

	err := NewDiagnosticError(ErrTypeModemNotResponding, "error 1")

	// First error
	if !notifier.NotifyError(ctx, err) {
		t.Error("First error should be sent")
	}

	// Same error - not sent
	if notifier.NotifyError(ctx, err) {
		t.Error("Duplicate should not be sent")
	}

	// Recovery
	notifier.NotifyRecovery(ctx)

	// Same error type after recovery - SHOULD be sent
	if !notifier.NotifyError(ctx, err) {
		t.Error("Error after recovery should be sent")
	}
}

func TestErrorTypeName(t *testing.T) {
	tests := []struct {
		errType DiagnosticErrorType
		want    string
	}{
		{ErrTypeSerialPort, "Serial Port Error"},
		{ErrTypeModemNotResponding, "Modem Not Responding"},
		{ErrTypeSimNotDetected, "SIM Not Detected"},
		{ErrTypeSimPinRequired, "SIM PIN Required"},
		{ErrTypeSimPukLocked, "SIM PUK Locked"},
		{ErrTypeNetworkDenied, "Network Denied"},
		{ErrTypeNetworkNotRegistered, "Network Not Registered"},
		{ErrTypeNoSignal, "No Signal"},
		{ErrTypeNone, "Unknown"},
	}

	for _, tt := range tests {
		t.Run(tt.want, func(t *testing.T) {
			got := errorTypeName(tt.errType)
			if got != tt.want {
				t.Errorf("errorTypeName(%v) = %q, want %q", tt.errType, got, tt.want)
			}
		})
	}
}

func TestErrorNotifier_FormatMessage(t *testing.T) {
	notifier := NewErrorNotifier(nil, []int64{123}, true, "test-host")

	tests := []struct {
		errType     DiagnosticErrorType
		wantContain string
	}{
		{ErrTypeSerialPort, "Serial Port Error"},
		{ErrTypeModemNotResponding, "Modem Not Responding"},
		{ErrTypeSimNotDetected, "SIM Card Not Detected"},
		{ErrTypeSimPinRequired, "SIM PIN Required"},
		{ErrTypeSimPukLocked, "SIM PUK Locked"},
		{ErrTypeNetworkDenied, "Network Registration Denied"},
	}

	for _, tt := range tests {
		t.Run(tt.wantContain, func(t *testing.T) {
			err := NewDiagnosticError(tt.errType, "test message")
			msg := notifier.formatErrorMessage(err)

			if !contains(msg, tt.wantContain) {
				t.Errorf("formatErrorMessage() should contain %q, got %q", tt.wantContain, msg)
			}
			if !contains(msg, "test-host") {
				t.Errorf("formatErrorMessage() should contain hostname")
			}
			if !contains(msg, "SMS Gateway Alert") {
				t.Errorf("formatErrorMessage() should contain 'SMS Gateway Alert'")
			}
		})
	}
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(s) > 0 && containsHelper(s, substr))
}

func containsHelper(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
