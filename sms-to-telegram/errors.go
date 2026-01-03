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
)

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
	mu            sync.Mutex
	lastErrorType DiagnosticErrorType
	tgBot         *bot.Bot
	chatIDs       []int64
	dryRun        bool
	hostname      string
	sendTimeout   time.Duration
}

// NewErrorNotifier creates a new error notifier
func NewErrorNotifier(tgBot *bot.Bot, chatIDs []int64, dryRun bool, hostname string, sendTimeout time.Duration) *ErrorNotifier {
	if sendTimeout <= 0 {
		sendTimeout = 20 * time.Second
	}
	return &ErrorNotifier{
		lastErrorType: ErrTypeNone,
		tgBot:         tgBot,
		chatIDs:       chatIDs,
		dryRun:        dryRun,
		hostname:      hostname,
		sendTimeout:   sendTimeout,
	}
}

// NotifyError sends error notification to Telegram if error type changed
// Returns true if notification was sent
func (n *ErrorNotifier) NotifyError(ctx context.Context, err *DiagnosticError) bool {
	n.mu.Lock()
	defer n.mu.Unlock()

	// Don't notify if same error type as before
	if err.Type == n.lastErrorType {
		slog.Debug("Skipping duplicate error notification", "type", errorTypeName(err.Type))
		return false
	}

	slog.Info("Sending error notification", "type", errorTypeName(err.Type), "previous", errorTypeName(n.lastErrorType))

	// Format error message
	msg := n.formatErrorMessage(err)

	// Send to Telegram
	if err := n.sendToTelegram(ctx, msg); err != nil {
		slog.Error("Failed to send error notification to Telegram", "error", err)
		return false
	}

	n.lastErrorType = err.Type

	return true
}

// NotifyRecovery sends recovery notification if there was a previous error
func (n *ErrorNotifier) NotifyRecovery(ctx context.Context) bool {
	n.mu.Lock()
	defer n.mu.Unlock()

	// Don't notify if there was no previous error
	if n.lastErrorType == ErrTypeNone {
		slog.Debug("Skipping recovery notification - no previous error")
		return false
	}

	prevError := n.lastErrorType
	slog.Info("Sending recovery notification", "previous_error", errorTypeName(prevError))

	msg := fmt.Sprintf("<b>SMS Gateway Recovered</b>\n\n"+
		"<b>Host:</b> <code>%s</code>\n"+
		"<b>Status:</b> Modem is now operational\n"+
		"<b>Previous error:</b> %s",
		n.hostname,
		errorTypeName(prevError))

	if err := n.sendToTelegram(ctx, msg); err != nil {
		slog.Error("Failed to send recovery notification to Telegram", "error", err)
		return false
	}

	n.lastErrorType = ErrTypeNone

	return true
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
	default:
		title = "Unknown Error"
		details = err.Message
	}

	return fmt.Sprintf("<b>SMS Gateway Alert</b>\n\n"+
		"<b>Host:</b> <code>%s</code>\n"+
		"<b>Error:</b> %s\n"+
		"<b>Details:</b> %s\n\n"+
		"<i>%s</i>",
		n.hostname,
		title,
		details,
		err.Message)
}

func (n *ErrorNotifier) sendToTelegram(ctx context.Context, text string) error {
	if n.dryRun {
		slog.Info("DRY_RUN: Would send error notification", "text", text)
		return nil
	}

	if n.tgBot == nil {
		return fmt.Errorf("telegram bot not initialized")
	}

	var sendErrors []error
	for _, chatID := range n.chatIDs {
		sendCtx, cancel := context.WithTimeout(ctx, n.sendTimeout)
		_, err := n.tgBot.SendMessage(sendCtx, &bot.SendMessageParams{
			ChatID:    chatID,
			Text:      text,
			ParseMode: models.ParseModeHTML,
		})
		cancel()
		if err != nil {
			sendErrors = append(sendErrors, fmt.Errorf("failed to send to chat %d: %w", chatID, err))
		}
	}

	if len(sendErrors) > 0 {
		return errors.Join(sendErrors...)
	}

	return nil
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
	default:
		return "Unknown"
	}
}

// HasError returns true if there is an active error state
func (n *ErrorNotifier) HasError() bool {
	n.mu.Lock()
	defer n.mu.Unlock()
	return n.lastErrorType != ErrTypeNone
}
