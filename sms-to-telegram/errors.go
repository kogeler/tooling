// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/go-telegram/bot"
	"github.com/go-telegram/bot/models"
)

// DiagnosticError types for modem diagnostics
type DiagnosticErrorType int

const (
	ErrTypeNone DiagnosticErrorType = iota
	ErrTypeSerialPort
	ErrTypeModemNotResponding
	ErrTypeSimNotDetected
	ErrTypeSimPinRequired
	ErrTypeSimPukLocked
	ErrTypeNetworkDenied
	ErrTypeNetworkNotRegistered
	ErrTypeNoSignal
	ErrTypeModemInitFailed
	ErrTypeStorageLow
	ErrTypeDeliveryRejected
)

// SessionError wraps a transport-level AT session failure (timeout, poisoned
// stream, disconnect). Unlike DiagnosticError it does not alert immediately:
// the outer loop reopens the session and alerts only after several consecutive
// failed sessions, so a single transient blip stays quiet.
type SessionError struct {
	Err error
}

func (e *SessionError) Error() string { return fmt.Sprintf("modem session error: %v", e.Err) }
func (e *SessionError) Unwrap() error { return e.Err }

func NewSessionError(err error) *SessionError { return &SessionError{Err: err} }

// DiagnosticError represents a diagnostic error with type and message
type DiagnosticError struct {
	Type    DiagnosticErrorType
	Message string
}

func (e *DiagnosticError) Error() string {
	return e.Message
}

// NewDiagnosticError creates a new diagnostic error
func NewDiagnosticError(errType DiagnosticErrorType, format string, args ...interface{}) *DiagnosticError {
	return &DiagnosticError{
		Type:    errType,
		Message: fmt.Sprintf(format, args...),
	}
}

// ErrorNotifier tracks error states and sends notifications to Telegram
type ErrorNotifier struct {
	mu sync.Mutex
	// chatState tracks the last successfully delivered error type per chat,
	// so a chat that missed an alert (send failure) is retried while the
	// others are not re-notified.
	chatState         map[int64]DiagnosticErrorType
	storageLowAlerted bool
	sender            TelegramSender
	chatIDs           []int64
	dryRun            bool
	hostname          string
	sendTimeout       time.Duration
}

// NewErrorNotifier creates a new error notifier
func NewErrorNotifier(sender TelegramSender, chatIDs []int64, dryRun bool, hostname string, sendTimeout time.Duration) *ErrorNotifier {
	if sendTimeout <= 0 {
		sendTimeout = 20 * time.Second
	}
	return &ErrorNotifier{
		chatState:   make(map[int64]DiagnosticErrorType),
		sender:      sender,
		chatIDs:     chatIDs,
		dryRun:      dryRun,
		hostname:    hostname,
		sendTimeout: sendTimeout,
	}
}

// alertGroup maps diagnostic error types onto deduplication groups. No-signal
// and not-registered are physically one flapping condition (weak/absent
// coverage): a marginal site alternates between CSQ=99 and CREG=2 across
// diagnostic runs, and alternating alert types must not pierce deduplication.
func alertGroup(t DiagnosticErrorType) DiagnosticErrorType {
	switch t {
	case ErrTypeNoSignal, ErrTypeNetworkNotRegistered:
		return ErrTypeNoSignal // canonical representative of the radio group
	default:
		return t
	}
}

// NotifyError sends error notification to every chat whose last delivered
// state is in a different dedup group than this error. Returns true if at
// least one chat was notified. A chat whose send fails keeps its old state
// and is retried on the next NotifyError call.
func (n *ErrorNotifier) NotifyError(ctx context.Context, diagErr *DiagnosticError) bool {
	n.mu.Lock()
	defer n.mu.Unlock()

	msg := n.formatErrorMessage(diagErr)
	notified := false
	for _, chatID := range n.chatIDs {
		if alertGroup(n.chatState[chatID]) == alertGroup(diagErr.Type) {
			// Same condition (possibly a refined sibling type): remember the
			// latest type silently so recovery names the current state.
			n.chatState[chatID] = diagErr.Type
			slog.Debug("Skipping duplicate error notification",
				"chat_id", chatID, "type", errorTypeName(diagErr.Type))
			continue
		}
		slog.Info("Sending error notification",
			"chat_id", chatID,
			"type", errorTypeName(diagErr.Type),
			"previous", errorTypeName(n.chatState[chatID]),
		)
		if err := n.sendToChat(ctx, chatID, msg); err != nil {
			slog.Error("Failed to send error notification to Telegram",
				"chat_id", chatID, "error", err)
			continue
		}
		n.chatState[chatID] = diagErr.Type
		notified = true
	}
	return notified
}

// NotifyRecovery sends a recovery notification to every chat that previously
// received an error. Returns true if at least one chat was notified.
func (n *ErrorNotifier) NotifyRecovery(ctx context.Context) bool {
	n.mu.Lock()
	defer n.mu.Unlock()

	notified := false
	for _, chatID := range n.chatIDs {
		prevError := n.chatState[chatID]
		if prevError == ErrTypeNone {
			continue
		}
		slog.Info("Sending recovery notification",
			"chat_id", chatID, "previous_error", errorTypeName(prevError))

		msg := fmt.Sprintf("<b>SMS Gateway Recovered</b>\n\n"+
			"<b>Host:</b> <code>%s</code>\n"+
			"<b>Status:</b> Modem is now operational\n"+
			"<b>Previous error:</b> %s",
			escapeHTML(n.hostname),
			errorTypeName(prevError))

		if err := n.sendToChat(ctx, chatID, msg); err != nil {
			slog.Error("Failed to send recovery notification to Telegram",
				"chat_id", chatID, "error", err)
			continue
		}
		n.chatState[chatID] = ErrTypeNone
		notified = true
	}
	if !notified {
		slog.Debug("Skipping recovery notification - no previous error")
	}
	return notified
}

func (n *ErrorNotifier) formatErrorMessage(err *DiagnosticError) string {
	var title, details string

	switch err.Type {
	case ErrTypeSerialPort:
		title = "Serial Port Error"
		details = "Cannot open serial port. Check if modem is connected and port is correct."
	case ErrTypeModemNotResponding:
		title = "Modem Not Responding"
		details = "Modem is not responding to AT commands. Check power and USB connection."
	case ErrTypeSimNotDetected:
		title = "SIM Card Not Detected"
		details = "SIM card is not inserted or not detected. Check SIM card installation."
	case ErrTypeSimPinRequired:
		title = "SIM PIN Required"
		details = "SIM card requires PIN code. Disable PIN or configure PIN entry."
	case ErrTypeSimPukLocked:
		title = "SIM PUK Locked"
		details = "SIM card is PUK locked. Use carrier PUK code to unlock."
	case ErrTypeNetworkDenied:
		title = "Network Registration Denied"
		details = "Network operator denied registration. Check SIM activation and account status."
	case ErrTypeNetworkNotRegistered:
		title = "Network Not Registered"
		details = "Modem is not registered on network. Check signal and antenna."
	case ErrTypeNoSignal:
		title = "No Signal"
		details = "No cellular signal detected. Check antenna and coverage."
	case ErrTypeModemInitFailed:
		title = "Modem Initialization Failed"
		details = "Modem refused a mandatory session setup command (PDU mode, SIM storage or CNMI). SMS polling cannot start safely."
	case ErrTypeStorageLow:
		title = "SIM Storage Low"
		details = "SIM message storage is almost full. New SMS may be rejected by the network. Investigate stuck messages."
	case ErrTypeDeliveryRejected:
		title = "SMS Delivery Rejected by Telegram"
		details = "Telegram permanently rejected a forwarded SMS. The SMS is kept on the SIM and occupies a slot until removed manually."
	default:
		title = "Unknown Error"
		details = err.Message
	}

	// Every dynamic value is escaped: err.Message regularly embeds raw modem
	// output, and an unescaped < or & would make Telegram reject the alert
	// exactly when the operator needs it.
	return fmt.Sprintf("<b>SMS Gateway Alert</b>\n\n"+
		"<b>Host:</b> <code>%s</code>\n"+
		"<b>Error:</b> %s\n"+
		"<b>Details:</b> %s\n\n"+
		"<i>%s</i>",
		escapeHTML(n.hostname),
		escapeHTML(title),
		escapeHTML(details),
		escapeHTML(err.Message))
}

// sendToChat delivers one notification to one chat.
func (n *ErrorNotifier) sendToChat(ctx context.Context, chatID int64, text string) error {
	if n.dryRun {
		slog.Info("DRY_RUN: Would send notification", "chat_id", chatID, "text_length", len(text))
		slog.Debug("DRY_RUN notification content", "text", text)
		return nil
	}

	if n.sender == nil {
		return fmt.Errorf("telegram bot not initialized")
	}

	sendCtx, cancel := context.WithTimeout(ctx, n.sendTimeout)
	defer cancel()
	_, err := n.sender.SendMessage(sendCtx, &bot.SendMessageParams{
		ChatID:    chatID,
		Text:      text,
		ParseMode: models.ParseModeHTML,
	})
	if err != nil {
		return fmt.Errorf("failed to send to chat %d: %w", chatID, err)
	}
	return nil
}

// sendToTelegram broadcasts a notification to every chat (used for stateless
// alerts like storage warnings and rejected-message notices).
func (n *ErrorNotifier) sendToTelegram(ctx context.Context, text string) error {
	var sendErrors []error
	for _, chatID := range n.chatIDs {
		if err := n.sendToChat(ctx, chatID, text); err != nil {
			sendErrors = append(sendErrors, err)
		}
	}
	return errors.Join(sendErrors...)
}

func errorTypeName(t DiagnosticErrorType) string {
	switch t {
	case ErrTypeSerialPort:
		return "Serial Port Error"
	case ErrTypeModemNotResponding:
		return "Modem Not Responding"
	case ErrTypeSimNotDetected:
		return "SIM Not Detected"
	case ErrTypeSimPinRequired:
		return "SIM PIN Required"
	case ErrTypeSimPukLocked:
		return "SIM PUK Locked"
	case ErrTypeNetworkDenied:
		return "Network Denied"
	case ErrTypeNetworkNotRegistered:
		return "Network Not Registered"
	case ErrTypeNoSignal:
		return "No Signal"
	case ErrTypeModemInitFailed:
		return "Modem Init Failed"
	case ErrTypeStorageLow:
		return "SIM Storage Low"
	case ErrTypeDeliveryRejected:
		return "Delivery Rejected"
	default:
		return "Unknown"
	}
}

// HasError returns true if any chat is in an active error state
func (n *ErrorNotifier) HasError() bool {
	n.mu.Lock()
	defer n.mu.Unlock()
	for _, state := range n.chatState {
		if state != ErrTypeNone {
			return true
		}
	}
	return false
}

// SIM storage alerting thresholds (percent), with hysteresis so the alert
// does not flap around the boundary.
const (
	storageLowAlertPercent = 80
	storageLowClearPercent = 70
)

// CheckStorage tracks SIM storage usage and alerts once when it crosses the
// high-water mark; the alert re-arms after usage drops below the clear mark.
// This is independent of the main error-type state machine: a filling SIM is
// a warning condition, not a session failure.
func (n *ErrorNotifier) CheckStorage(ctx context.Context, used, total int) {
	if total <= 0 || used < 0 {
		return
	}
	percent := used * 100 / total

	n.mu.Lock()
	alerted := n.storageLowAlerted
	switch {
	case !alerted && percent >= storageLowAlertPercent:
		n.storageLowAlerted = true
	case alerted && percent < storageLowClearPercent:
		n.storageLowAlerted = false
	}
	shouldAlert := !alerted && n.storageLowAlerted
	n.mu.Unlock()

	if !shouldAlert {
		return
	}

	slog.Warn("SIM storage almost full", "used", used, "total", total)
	msg := fmt.Sprintf("<b>SMS Gateway Alert</b>\n\n"+
		"<b>Host:</b> <code>%s</code>\n"+
		"<b>Warning:</b> SIM storage almost full (%d/%d slots used)\n\n"+
		"<i>New SMS may be rejected once the SIM is full. Check for stuck or rejected messages.</i>",
		escapeHTML(n.hostname), used, total)
	if err := n.sendToTelegram(ctx, msg); err != nil {
		slog.Error("Failed to send storage alert", "error", err)
		// Re-arm so the alert is retried on the next check.
		n.mu.Lock()
		n.storageLowAlerted = false
		n.mu.Unlock()
	}
}
