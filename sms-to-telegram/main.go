// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/go-telegram/bot"
	"github.com/go-telegram/bot/models"
	"github.com/tarm/serial"
)

type Config struct {
	TelegramToken string
	ChatIDs       []int64
	SerialPort    string
	BaudRate      int
	LogLevel      slog.Level
	DryRun        bool // for testing without telegram
	// Max age for stale multipart SMS parts before deletion. 0 disables cleanup.
	MultipartMaxAge time.Duration
	// Timeout for a single Telegram API call.
	TelegramSendTimeout time.Duration
	// Grace period to wait for network registration before alerting. 0 disables grace.
	NetworkRegGrace time.Duration
}

func main() {
	cfg, err := loadConfig()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Configuration error: %v\n", err)
		os.Exit(1)
	}

	setupLogging(cfg.LogLevel)

	slog.Info("Starting SMS to Telegram forwarder",
		"serial_port", cfg.SerialPort,
		"baud_rate", cfg.BaudRate,
		"chat_ids", cfg.ChatIDs,
		"dry_run", cfg.DryRun,
		"multipart_max_age", cfg.MultipartMaxAge,
		"telegram_send_timeout", cfg.TelegramSendTimeout,
		"network_reg_grace", cfg.NetworkRegGrace,
	)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		sig := <-sigChan
		slog.Info("Received signal, shutting down", "signal", sig)
		cancel()
	}()

	if err := run(ctx, cfg); err != nil {
		slog.Error("Fatal error", "error", err)
		os.Exit(1)
	}
}

func loadConfig() (*Config, error) {
	dryRun := os.Getenv("DRY_RUN") == "true" || os.Getenv("DRY_RUN") == "1"

	token := os.Getenv("TELEGRAM_BOT_TOKEN")
	if token == "" && !dryRun {
		return nil, fmt.Errorf("TELEGRAM_BOT_TOKEN environment variable is required")
	}

	chatIDsStr := os.Getenv("TELEGRAM_CHAT_IDS")
	if chatIDsStr == "" && !dryRun {
		return nil, fmt.Errorf("TELEGRAM_CHAT_IDS environment variable is required (comma-separated list)")
	}

	var chatIDs []int64
	if chatIDsStr != "" {
		for _, idStr := range strings.Split(chatIDsStr, ",") {
			idStr = strings.TrimSpace(idStr)
			if idStr == "" {
				continue
			}
			id, err := strconv.ParseInt(idStr, 10, 64)
			if err != nil {
				return nil, fmt.Errorf("invalid chat ID %q: %w", idStr, err)
			}
			chatIDs = append(chatIDs, id)
		}
	}
	if len(chatIDs) == 0 && !dryRun {
		return nil, fmt.Errorf("at least one chat ID is required")
	}

	serialPort := os.Getenv("SERIAL_PORT")
	if serialPort == "" {
		serialPort = "/dev/ttyUSB0"
	}

	baudRate := 115200
	if baudStr := os.Getenv("BAUD_RATE"); baudStr != "" {
		var err error
		baudRate, err = strconv.Atoi(baudStr)
		if err != nil {
			return nil, fmt.Errorf("invalid BAUD_RATE %q: %w", baudStr, err)
		}
	}

	logLevel := slog.LevelInfo
	if logLevelStr := os.Getenv("LOG_LEVEL"); logLevelStr != "" {
		switch strings.ToUpper(logLevelStr) {
		case "DEBUG":
			logLevel = slog.LevelDebug
		case "INFO":
			logLevel = slog.LevelInfo
		case "WARN", "WARNING":
			logLevel = slog.LevelWarn
		case "ERROR":
			logLevel = slog.LevelError
		default:
			return nil, fmt.Errorf("invalid LOG_LEVEL %q (use DEBUG, INFO, WARN, ERROR)", logLevelStr)
		}
	}

	var multipartMaxAge time.Duration
	if maxAgeStr := os.Getenv("MULTIPART_MAX_AGE"); maxAgeStr != "" {
		var err error
		multipartMaxAge, err = time.ParseDuration(maxAgeStr)
		if err != nil {
			return nil, fmt.Errorf("invalid MULTIPART_MAX_AGE %q: %w", maxAgeStr, err)
		}
		if multipartMaxAge < 0 {
			return nil, fmt.Errorf("invalid MULTIPART_MAX_AGE %q: must be >= 0", maxAgeStr)
		}
	}

	telegramSendTimeout := 20 * time.Second
	if timeoutStr := os.Getenv("TELEGRAM_SEND_TIMEOUT"); timeoutStr != "" {
		var err error
		telegramSendTimeout, err = time.ParseDuration(timeoutStr)
		if err != nil {
			return nil, fmt.Errorf("invalid TELEGRAM_SEND_TIMEOUT %q: %w", timeoutStr, err)
		}
		if telegramSendTimeout <= 0 {
			return nil, fmt.Errorf("invalid TELEGRAM_SEND_TIMEOUT %q: must be > 0", timeoutStr)
		}
	}

	networkRegGrace := 90 * time.Second
	if graceStr := os.Getenv("NETWORK_REG_GRACE"); graceStr != "" {
		var err error
		networkRegGrace, err = time.ParseDuration(graceStr)
		if err != nil {
			return nil, fmt.Errorf("invalid NETWORK_REG_GRACE %q: %w", graceStr, err)
		}
		if networkRegGrace < 0 {
			return nil, fmt.Errorf("invalid NETWORK_REG_GRACE %q: must be >= 0", graceStr)
		}
	}

	return &Config{
		TelegramToken:       token,
		ChatIDs:             chatIDs,
		SerialPort:          serialPort,
		BaudRate:            baudRate,
		LogLevel:            logLevel,
		DryRun:              dryRun,
		MultipartMaxAge:     multipartMaxAge,
		TelegramSendTimeout: telegramSendTimeout,
		NetworkRegGrace:     networkRegGrace,
	}, nil
}

func setupLogging(level slog.Level) {
	opts := &slog.HandlerOptions{
		Level: level,
	}
	handler := slog.NewTextHandler(os.Stderr, opts)
	slog.SetDefault(slog.New(handler))
}

// runModemDiagnostics performs detailed modem diagnostics and returns DiagnosticError.
// networkGrace defines how long to wait for network registration after sessionStart.
func runModemDiagnostics(ctx context.Context, modem *SimpleAT, sessionStart time.Time, networkGrace time.Duration) *DiagnosticError {
	slog.Info("Testing modem connection...")

	// Test modem with simple AT command
	slog.Debug("Testing modem with AT command...")
	if resp, cmdErr := modem.Command("AT"); cmdErr != nil {
		slog.Error("Modem not responding to AT command", "error", cmdErr)
		return NewDiagnosticError(ErrTypeModemNotResponding,
			"Modem not responding to AT commands: %v", cmdErr)
	} else {
		slog.Debug("Modem responds to AT", "response", resp)
	}

	// Disable echo to avoid confusion
	modem.Command("ATE0")

	slog.Info("Modem connection OK")

	// Get modem info
	if resp, err := modem.Command("ATI"); err == nil {
		slog.Info("Modem info", "model", strings.Join(resp, " "))
	}

	// Check SIM card status - this is critical
	// Note: SIM may take a few seconds to initialize after modem power-on
	slog.Debug("Checking SIM card (may retry if not ready yet)...")

	var simResp []string
	var simErr error
	var simReady bool

	// Retry SIM check up to 5 times with delays (SIM initialization can take time)
	for attempt := 1; attempt <= 5; attempt++ {
		simResp, simErr = modem.Command("AT+CPIN?")
		if simErr == nil {
			// Success - SIM responded
			break
		}

		if attempt < 5 {
			slog.Debug("SIM not ready yet, waiting...", "attempt", attempt)
			time.Sleep(2 * time.Second)
		}
	}

	if simErr != nil {
		// AT+CPIN? still returns ERROR after retries - try to understand why
		slog.Warn("AT+CPIN? returned ERROR after retries, checking if SIM is physically present...")

		// Try AT+CCID to check if SIM is physically detected
		ccidResp, ccidErr := modem.Command("AT+CCID")
		if ccidErr != nil {
			// Both CPIN and CCID fail - SIM physically not detected
			slog.Error("SIM card not detected", "cpin_error", simErr, "ccid_error", ccidErr)
			return NewDiagnosticError(ErrTypeSimNotDetected,
				"SIM card not physically detected (AT+CPIN? and AT+CCID both fail)")
		}

		slog.Info("SIM card physically detected", "ICCID", ccidResp)
		slog.Warn("But AT+CPIN? fails - SIM may be initializing")
		return NewDiagnosticError(ErrTypeSimNotDetected,
			"SIM card detected but not ready (AT+CPIN? fails)")
	}

	// Parse SIM status - must contain "+CPIN:" prefix
	simStatus := strings.Join(simResp, " ")
	slog.Info("SIM status raw", "status", simStatus)

	// Validate that response is actually from AT+CPIN? command
	// After modem reset, we might get garbage or previous command output
	if !strings.Contains(simStatus, "+CPIN:") {
		slog.Error("Invalid AT+CPIN? response - not a CPIN response", "response", simStatus)
		return NewDiagnosticError(ErrTypeSimNotDetected,
			"Invalid SIM status response: %s (expected +CPIN:)", simStatus)
	}

	if strings.Contains(simStatus, "READY") {
		slog.Info("SIM card is READY")
		simReady = true
	} else if strings.Contains(simStatus, "SIM PIN") {
		slog.Error("SIM card requires PIN")
		return NewDiagnosticError(ErrTypeSimPinRequired,
			"SIM card requires PIN code")
	} else if strings.Contains(simStatus, "SIM PUK") {
		slog.Error("SIM card is PUK locked")
		return NewDiagnosticError(ErrTypeSimPukLocked,
			"SIM card is PUK locked (too many wrong PIN attempts)")
	} else if strings.Contains(simStatus, "NOT INSERTED") {
		slog.Error("No SIM card inserted")
		return NewDiagnosticError(ErrTypeSimNotDetected,
			"No SIM card inserted in modem")
	} else if strings.Contains(simStatus, "NOT READY") {
		slog.Warn("SIM card not ready yet")
		return NewDiagnosticError(ErrTypeSimNotDetected,
			"SIM card not ready (still initializing)")
	} else {
		// Unknown status - treat as error
		slog.Error("Unknown SIM status", "status", simStatus)
		return NewDiagnosticError(ErrTypeSimNotDetected,
			"Unknown SIM status: %s", simStatus)
	}

	// Check signal quality
	var noSignal bool
	var signalChecked bool
	var signalRSSI string
	if resp, err := modem.Command("AT+CSQ"); err == nil {
		slog.Info("Signal quality", "response", strings.Join(resp, " "))
		// Parse signal: +CSQ: rssi,ber
		// rssi: 0-31 (0=-113dBm, 31=-51dBm), 99=unknown
		for _, line := range resp {
			if strings.HasPrefix(line, "+CSQ:") {
				signalChecked = true
				parts := strings.Split(strings.TrimPrefix(line, "+CSQ:"), ",")
				if len(parts) >= 1 {
					rssi := strings.TrimSpace(parts[0])
					signalRSSI = rssi
					if rssi == "99" {
						slog.Warn("SIGNAL: No signal or not detectable")
						noSignal = true
					} else if rssi == "0" {
						slog.Warn("SIGNAL: Very weak signal (-113 dBm or less)")
					} else {
						slog.Info("SIGNAL: Signal detected", "rssi", rssi)
					}
				}
			}
		}
	} else {
		slog.Warn("Could not check signal quality", "error", err)
	}

	if noSignal && signalChecked {
		return NewDiagnosticError(ErrTypeNoSignal,
			"No signal detected (CSQ=%s)", signalRSSI)
	}

	// Check network registration
	var networkRegistered bool
	var cregChecked bool
	var networkStat string
	checkNetwork := func() *DiagnosticError {
		cregChecked = false
		networkRegistered = false
		networkStat = ""

		resp, err := modem.Command("AT+CREG?")
		if err != nil {
			slog.Warn("Could not check network registration", "error", err)
			return nil
		}
		slog.Info("Network registration", "response", strings.Join(resp, " "))
		// Parse: +CREG: n,stat
		// stat: 0=not registered, 1=registered home, 2=searching, 3=denied, 4=unknown, 5=roaming
		for _, line := range resp {
			if strings.HasPrefix(line, "+CREG:") {
				cregChecked = true
				parts := strings.Split(strings.TrimPrefix(line, "+CREG:"), ",")
				if len(parts) >= 2 {
					stat := strings.TrimSpace(parts[1])
					networkStat = stat
					switch stat {
					case "0":
						slog.Warn("NETWORK: Not registered, not searching")
						if simReady {
							slog.Warn("SIM is ready but not registered - network issue or no coverage")
						}
					case "1":
						slog.Info("NETWORK: Registered on home network")
						networkRegistered = true
					case "2":
						slog.Warn("NETWORK: Not registered, searching for network...")
					case "3":
						slog.Error("NETWORK: Registration denied by operator")
						return NewDiagnosticError(ErrTypeNetworkDenied,
							"Network operator denied registration")
					case "4":
						slog.Warn("NETWORK: Unknown registration status")
					case "5":
						slog.Info("NETWORK: Registered, roaming")
						networkRegistered = true
					}
				}
			}
		}

		return nil
	}

	if diagErr := checkNetwork(); diagErr != nil {
		return diagErr
	}

	waitedForNetwork := false
	for simReady && cregChecked && !networkRegistered && networkGrace > 0 {
		elapsed := time.Since(sessionStart)
		if elapsed >= networkGrace {
			break
		}
		if networkStat != "0" && networkStat != "2" && networkStat != "4" {
			break
		}

		remaining := networkGrace - elapsed
		wait := 5 * time.Second
		if remaining < wait {
			wait = remaining
		}
		if !waitedForNetwork {
			slog.Info("Waiting for network registration", "grace", networkGrace, "elapsed", elapsed)
			waitedForNetwork = true
		}

		select {
		case <-ctx.Done():
			return nil
		case <-time.After(wait):
		}

		if diagErr := checkNetwork(); diagErr != nil {
			return diagErr
		}
	}

	// Treat missing registration as an error when SIM is ready and CREG was checked.
	if simReady && cregChecked && !networkRegistered {
		if networkStat == "" {
			networkStat = "unknown"
		}
		return NewDiagnosticError(ErrTypeNetworkNotRegistered,
			"Not registered on network (CREG=%s)", networkStat)
	}

	// Warn if SIM is ready but not registered
	if simReady && !networkRegistered {
		slog.Warn("SIM card is ready but NOT registered on network")
		slog.Warn("This may be normal if modem just started - wait 30-60 seconds")
		slog.Warn("If problem persists: check coverage, antenna, or SIM activation")
	}

	// Check operator
	if resp, err := modem.Command("AT+COPS?"); err == nil {
		slog.Info("Operator", "response", strings.Join(resp, " "))
	}

	return nil
}

func run(ctx context.Context, cfg *Config) error {
	// Get hostname for error notifications
	hostname, _ := os.Hostname()
	if hostname == "" {
		hostname = "unknown"
	}

	// Initialize Telegram bot (unless dry run)
	var tgBot *bot.Bot
	if !cfg.DryRun {
		var err error
		tgBot, err = bot.New(cfg.TelegramToken)
		if err != nil {
			return fmt.Errorf("failed to create telegram bot: %w", err)
		}
		slog.Info("Telegram bot initialized")
	} else {
		slog.Warn("Running in DRY_RUN mode - messages will not be sent to Telegram")
	}

	// Create error notifier for sending diagnostic errors to Telegram
	notifier := NewErrorNotifier(tgBot, cfg.ChatIDs, cfg.DryRun, hostname, cfg.TelegramSendTimeout)

	// Retry interval for modem connection issues
	retryInterval := 30 * time.Second

	// Track if we need to reset modem on next attempt
	needReset := false

	// Main loop with retry logic
	for {
		select {
		case <-ctx.Done():
			slog.Info("Context cancelled, exiting")
			return nil
		default:
		}

		// Try to run the modem polling loop
		err := runModemLoop(ctx, cfg, tgBot, notifier, needReset)

		if err == nil {
			// Normal exit (context cancelled)
			return nil
		}

		// Check if it's a diagnostic error
		if diagErr, ok := err.(*DiagnosticError); ok {
			slog.Error("Modem diagnostic error", "type", errorTypeName(diagErr.Type), "error", diagErr.Message)
			notifier.NotifyError(ctx, diagErr)

			// Determine if we need modem reset on next attempt
			// SIM-related errors benefit from full modem reset
			switch diagErr.Type {
			case ErrTypeSimNotDetected, ErrTypeSimPinRequired, ErrTypeSimPukLocked, ErrTypeNetworkDenied:
				needReset = true
				slog.Info("Will perform modem reset on next attempt")
			default:
				needReset = false
			}

			// Wait before retry
			slog.Info("Will retry modem connection", "retry_in", retryInterval)
			select {
			case <-ctx.Done():
				return nil
			case <-time.After(retryInterval):
			}
			continue
		}

		// Non-diagnostic error - log and retry (no reset needed)
		slog.Error("Modem loop error", "error", err)
		needReset = false
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(retryInterval):
		}
	}
}

// runModemLoop handles serial port connection and SMS polling
// needReset indicates if modem should be reset (e.g., after SIM error)
func runModemLoop(ctx context.Context, cfg *Config, tgBot *bot.Bot, notifier *ErrorNotifier, needReset bool) error {
	// Open serial port
	slog.Debug("Opening serial port", "port", cfg.SerialPort, "baud", cfg.BaudRate)
	serialCfg := &serial.Config{
		Name:        cfg.SerialPort,
		Baud:        cfg.BaudRate,
		ReadTimeout: time.Millisecond * 500,
	}
	p, err := serial.OpenPort(serialCfg)
	if err != nil {
		return NewDiagnosticError(ErrTypeSerialPort,
			"Failed to open serial port %s: %v", cfg.SerialPort, err)
	}
	defer p.Close()
	slog.Info("Serial port opened successfully")

	// Create simple AT modem interface
	modem := NewSimpleAT(p, 5*time.Second)

	// Reset modem if requested (e.g., after SIM error)
	// Use AT+CFUN to do a full modem reset which re-initializes SIM
	if needReset {
		slog.Info("Performing full modem reset (AT+CFUN) to recover from previous error...")
		modem.Command("AT+CFUN=0") // Minimum functionality (turns off RF)
		time.Sleep(2 * time.Second)
		modem.Command("AT+CFUN=1")  // Full functionality (re-init SIM)
		time.Sleep(5 * time.Second) // Give modem time to reset and detect SIM

		// Flush any garbage from modem buffer after reset
		// Send a few AT commands to synchronize
		for i := 0; i < 3; i++ {
			modem.Command("AT")
			time.Sleep(200 * time.Millisecond)
		}
		// Disable echo
		modem.Command("ATE0")
		time.Sleep(200 * time.Millisecond)

		slog.Info("Modem reset complete")
	}

	sessionStart := time.Now()

	// Run detailed modem diagnostics
	slog.Info("Running modem diagnostics...")
	if diagErr := runModemDiagnostics(ctx, modem, sessionStart, cfg.NetworkRegGrace); diagErr != nil {
		return diagErr
	}

	// Notify recovery if there was a previous error
	notifier.NotifyRecovery(ctx)

	// Set PDU mode (AT+CMGF=0)
	if _, err := modem.Command("AT+CMGF=0"); err != nil {
		slog.Warn("Failed to set PDU mode", "error", err)
	} else {
		slog.Debug("PDU mode set")
	}

	// Set preferred message storage to SIM
	if _, err := modem.Command("AT+CPMS=\"SM\",\"SM\",\"SM\""); err != nil {
		slog.Warn("Failed to set message storage", "error", err)
	} else {
		slog.Debug("Message storage set to SIM")
	}

	// Main loop: poll for SMS messages
	pollInterval := 10 * time.Second
	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()

	// Periodic modem health check
	healthCheckInterval := 60 * time.Second
	healthTicker := time.NewTicker(healthCheckInterval)
	defer healthTicker.Stop()

	// Track consecutive timeout errors (not modem ERROR responses)
	consecutiveTimeouts := 0
	const maxConsecutiveTimeouts = 3

	slog.Info("Starting SMS polling loop",
		"poll_interval", pollInterval,
		"health_check_interval", healthCheckInterval,
	)

	// handleError analyzes the error and returns DiagnosticError if we should exit the loop
	handleError := func(err error) *DiagnosticError {
		slog.Error("Error processing messages", "error", err)

		// Check if it's a timeout/disconnect error (modem not responding)
		if IsTimeoutError(err) {
			consecutiveTimeouts++
			slog.Warn("Modem timeout", "consecutive", consecutiveTimeouts, "max", maxConsecutiveTimeouts)
			if consecutiveTimeouts >= maxConsecutiveTimeouts {
				return NewDiagnosticError(ErrTypeModemNotResponding,
					"Modem not responding after %d attempts: %v", consecutiveTimeouts, err)
			}
			return nil // Continue polling, might recover
		}

		// Check if it's a modem ERROR response (modem responds but command fails)
		// This often indicates SIM/network issues - run diagnostics immediately
		if IsModemError(err) {
			slog.Warn("Modem returned ERROR - running diagnostics to determine cause")
			// Run diagnostics to get specific error
			if diagErr := runModemDiagnostics(ctx, modem, sessionStart, cfg.NetworkRegGrace); diagErr != nil {
				return diagErr
			}
			// Diagnostics passed but we still got ERROR - generic modem error
			return NewDiagnosticError(ErrTypeModemNotResponding,
				"Modem command failed: %v", err)
		}

		// Other errors (e.g., Telegram send failure) - don't exit loop
		return nil
	}

	// Process immediately on start
	if err := processMessages(ctx, modem, tgBot, cfg); err != nil {
		if diagErr := handleError(err); diagErr != nil {
			return diagErr
		}
	} else {
		consecutiveTimeouts = 0
	}

	for {
		select {
		case <-ctx.Done():
			slog.Info("Context cancelled, exiting polling loop")
			return nil

		case <-healthTicker.C:
			slog.Debug("Running modem health check")
			if err := modem.Ping(); err != nil {
				if IsTimeoutError(err) {
					slog.Error("Modem health check failed - not responding", "error", err)
					return NewDiagnosticError(ErrTypeModemNotResponding,
						"Modem health check failed: %v", err)
				}
				// Modem responds but with ERROR - run diagnostics
				slog.Warn("Modem health check returned ERROR - running diagnostics")
				if diagErr := runModemDiagnostics(ctx, modem, sessionStart, cfg.NetworkRegGrace); diagErr != nil {
					return diagErr
				}
			}
			slog.Debug("Modem health check passed")
			consecutiveTimeouts = 0

		case <-ticker.C:
			if err := processMessages(ctx, modem, tgBot, cfg); err != nil {
				if diagErr := handleError(err); diagErr != nil {
					return diagErr
				}
			} else {
				consecutiveTimeouts = 0
			}
		}
	}
}

type SMSMessage struct {
	Index       int
	From        string
	Text        string
	Time        time.Time
	SMSC        string // Service center number
	IsMultipart bool
	TotalParts  int
}

func processMessages(ctx context.Context, modem *SimpleAT, tgBot *bot.Bot, cfg *Config) error {
	slog.Debug("Checking for new SMS messages")

	// List all messages from SIM storage
	messages, indicesToDelete, err := listSMSMessages(modem, cfg.MultipartMaxAge)
	if err != nil {
		return fmt.Errorf("failed to list SMS messages: %w", err)
	}

	if len(messages) == 0 {
		if len(indicesToDelete) == 0 {
			slog.Debug("No messages found")
			return nil
		}
		slog.Warn("No complete messages, but stale multipart parts found", "count", len(indicesToDelete))
		if cfg.DryRun {
			slog.Info("DRY_RUN: Skipping SMS deletion", "indices", indicesToDelete)
			return nil
		}
		unique := make(map[int]struct{}, len(indicesToDelete))
		for _, idx := range indicesToDelete {
			if _, ok := unique[idx]; ok {
				continue
			}
			unique[idx] = struct{}{}
			slog.Debug("Deleting stale SMS part from SIM", "index", idx)
			if err := deleteSMS(modem, idx); err != nil {
				slog.Error("Failed to delete stale SMS part", "error", err, "index", idx)
			}
		}
		return nil
	}

	slog.Info("Found SMS messages", "count", len(messages))

	// Track which indices we've successfully sent
	var sentIndices []int

	for _, msg := range messages {
		slog.Debug("Processing SMS",
			"index", msg.Index,
			"from", msg.From,
			"time", msg.Time,
			"text_length", len(msg.Text),
		)

		// Format message for Telegram
		text := formatTelegramMessage(msg)

		// Send to all configured chats with retry
		if err := sendToTelegramWithRetry(ctx, tgBot, cfg, text); err != nil {
			// Don't delete message if sending failed
			slog.Error("Failed to send to Telegram after retries", "error", err, "index", msg.Index)
			return fmt.Errorf("failed to deliver SMS to Telegram: %w", err)
		}

		sentIndices = append(sentIndices, msg.Index)
		slog.Info("SMS forwarded successfully", "from", msg.From, "index", msg.Index)
	}

	// Delete all processed SMS indices (including multipart parts)
	// Only delete after ALL messages are successfully sent
	// NEVER delete in DRY_RUN mode
	if cfg.DryRun {
		slog.Info("DRY_RUN: Skipping SMS deletion", "indices", indicesToDelete)
	} else {
		unique := make(map[int]struct{}, len(indicesToDelete))
		for _, idx := range indicesToDelete {
			if _, ok := unique[idx]; ok {
				continue
			}
			unique[idx] = struct{}{}
			slog.Debug("Deleting SMS from SIM", "index", idx)
			if err := deleteSMS(modem, idx); err != nil {
				slog.Error("Failed to delete SMS", "error", err, "index", idx)
				// Continue deleting others - deletion failure is not critical
			}
		}
	}

	return nil
}

// RawSMS holds the raw parsed SMS with index for deletion
type RawSMS struct {
	Index int
	PDU   *PDUMessage
}

func listSMSMessages(modem *SimpleAT, maxAge time.Duration) ([]SMSMessage, []int, error) {
	// AT+CMGL=4 lists all messages in PDU mode
	// 4 = all messages
	resp, err := modem.Command("AT+CMGL=4")
	if err != nil {
		return nil, nil, fmt.Errorf("AT+CMGL command failed: %w", err)
	}

	slog.Debug("CMGL response", "lines", resp)

	// First pass: parse all PDUs
	var rawMessages []RawSMS
	lines := resp

	for i := 0; i < len(lines); i++ {
		line := lines[i]
		if !strings.HasPrefix(line, "+CMGL:") {
			continue
		}

		// Parse header: +CMGL: <index>,<stat>,<alpha>,<length>
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}

		fields := strings.Split(parts[1], ",")
		if len(fields) < 1 {
			continue
		}

		index, err := strconv.Atoi(strings.TrimSpace(fields[0]))
		if err != nil {
			slog.Warn("Failed to parse message index", "line", line, "error", err)
			continue
		}

		// Next line should be the PDU
		if i+1 >= len(lines) {
			continue
		}
		i++
		pduStr := strings.TrimSpace(lines[i])

		// Parse PDU using our custom parser
		pdu, err := ParsePDU(pduStr)
		if err != nil {
			slog.Warn("Failed to parse PDU", "pdu", pduStr, "error", err)
			// Still add message with raw data so we can forward it
			rawMessages = append(rawMessages, RawSMS{
				Index: index,
				PDU: &PDUMessage{
					Sender:    "unknown",
					Timestamp: time.Now(),
					Text:      fmt.Sprintf("[PDU parse error: %v]\nRaw: %s", err, pduStr),
				},
			})
			continue
		}

		rawMessages = append(rawMessages, RawSMS{Index: index, PDU: pdu})
	}

	// Second pass: assemble multipart messages
	collector := NewMultipartCollector()
	var messages []SMSMessage
	var indicesToDelete []int

	for _, raw := range rawMessages {
		if raw.PDU.IsMultipart {
			slog.Debug("Multipart SMS part",
				"index", raw.Index,
				"ref", raw.PDU.MultipartRef,
				"part", raw.PDU.PartNumber,
				"total", raw.PDU.TotalParts,
			)

			assembled, partIndices := collector.Add(raw.Index, raw.PDU)
			if assembled != nil {
				messages = append(messages, SMSMessage{
					Index:       raw.Index, // Use last part's index
					From:        assembled.Sender,
					Text:        assembled.Text,
					Time:        assembled.Timestamp,
					SMSC:        assembled.SMSC,
					IsMultipart: true,
					TotalParts:  raw.PDU.TotalParts,
				})
				indicesToDelete = append(indicesToDelete, partIndices...)
			}
		} else {
			messages = append(messages, SMSMessage{
				Index:       raw.Index,
				From:        raw.PDU.Sender,
				Text:        raw.PDU.Text,
				Time:        raw.PDU.Timestamp,
				SMSC:        raw.PDU.SMSC,
				IsMultipart: false,
				TotalParts:  1,
			})
			indicesToDelete = append(indicesToDelete, raw.Index)
		}
	}

	// Warn about incomplete multipart messages
	if pending := collector.Pending(); pending > 0 {
		slog.Warn("Some multipart messages are incomplete - waiting for more parts", "pending", pending)
	}

	if maxAge > 0 {
		now := time.Now()
		stale := collector.StaleIndices(maxAge, now)
		if len(stale) > 0 {
			slog.Warn("Stale multipart parts detected", "count", len(stale), "max_age", maxAge)
			indicesToDelete = append(indicesToDelete, stale...)
		}
	}

	return messages, indicesToDelete, nil
}

func deleteSMS(modem *SimpleAT, index int) error {
	cmd := fmt.Sprintf("AT+CMGD=%d", index)
	_, err := modem.Command(cmd)
	return err
}

func formatTelegramMessage(msg SMSMessage) string {
	var sb strings.Builder
	sb.WriteString("<b>SMS Received</b>\n\n")
	sb.WriteString(fmt.Sprintf("<b>From:</b> <code>%s</code>\n", escapeHTML(msg.From)))
	sb.WriteString(fmt.Sprintf("<b>Time:</b> %s\n", msg.Time.Format("2006-01-02 15:04:05")))
	if msg.SMSC != "" {
		sb.WriteString(fmt.Sprintf("<b>SMSC:</b> %s\n", escapeHTML(msg.SMSC)))
	}
	if msg.IsMultipart {
		sb.WriteString(fmt.Sprintf("<b>Parts:</b> %d\n", msg.TotalParts))
	}
	sb.WriteString(fmt.Sprintf("\n%s", escapeHTML(msg.Text)))
	return sb.String()
}

func escapeHTML(s string) string {
	replacer := strings.NewReplacer(
		"&", "&amp;",
		"<", "&lt;",
		">", "&gt;",
	)
	return replacer.Replace(s)
}

func sendToTelegramWithRetry(ctx context.Context, tgBot *bot.Bot, cfg *Config, text string) error {
	if cfg.DryRun {
		slog.Info("DRY_RUN: Would send to Telegram", "text", text, "chat_ids", cfg.ChatIDs)
		return nil
	}

	if tgBot == nil {
		return fmt.Errorf("telegram bot not initialized")
	}

	maxRetries := 10
	baseDelay := 5 * time.Second
	maxDelay := 5 * time.Minute

	var sendErrors []error

	for _, chatID := range cfg.ChatIDs {
		delay := baseDelay
		var lastErr error

		for attempt := 1; attempt <= maxRetries; attempt++ {
			select {
			case <-ctx.Done():
				return ctx.Err()
			default:
			}

			slog.Debug("Sending to Telegram", "chat_id", chatID, "attempt", attempt)

			sendCtx, cancel := context.WithTimeout(ctx, cfg.TelegramSendTimeout)
			_, err := tgBot.SendMessage(sendCtx, &bot.SendMessageParams{
				ChatID:    chatID,
				Text:      text,
				ParseMode: models.ParseModeHTML,
			})
			cancel()

			if err == nil {
				slog.Debug("Message sent successfully", "chat_id", chatID)
				lastErr = nil
				break
			}
			lastErr = err

			slog.Warn("Failed to send to Telegram",
				"chat_id", chatID,
				"attempt", attempt,
				"error", err,
				"next_retry_in", delay,
			)

			if attempt == maxRetries {
				break
			}

			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(delay):
			}

			// Exponential backoff with cap
			delay = delay * 2
			if delay > maxDelay {
				delay = maxDelay
			}
		}

		if lastErr != nil {
			sendErrors = append(sendErrors, fmt.Errorf("failed to send to chat %d after %d attempts: %w", chatID, maxRetries, lastErr))
		}
	}

	if len(sendErrors) > 0 {
		return errors.Join(sendErrors...)
	}

	return nil
}
