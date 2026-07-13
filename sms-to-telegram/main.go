// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
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
	"github.com/tarm/serial"
)

// contentFingerprint returns a short non-reversible identifier for sensitive
// content (SMS bodies, raw PDUs) so it can be correlated in logs without
// exposing the content itself. Full content is only ever logged at DEBUG.
func contentFingerprint(s string) string {
	sum := sha256.Sum256([]byte(s))
	return fmt.Sprintf("%x", sum[:6])
}

// maskICCID keeps only the last 4 digits of an ICCID for logging.
func maskICCID(lines []string) string {
	s := strings.Join(lines, " ")
	digits := make([]rune, 0, len(s))
	for _, r := range s {
		if r >= '0' && r <= '9' {
			digits = append(digits, r)
		}
	}
	if len(digits) <= 4 {
		return "****"
	}
	return "****" + string(digits[len(digits)-4:])
}

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
	dryRunStr := os.Getenv("DRY_RUN")
	dryRun := strings.EqualFold(dryRunStr, "true") || strings.EqualFold(dryRunStr, "yes") || dryRunStr == "1"

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
		seen := make(map[int64]struct{})
		for _, idStr := range strings.Split(chatIDsStr, ",") {
			idStr = strings.TrimSpace(idStr)
			if idStr == "" {
				continue
			}
			id, err := strconv.ParseInt(idStr, 10, 64)
			if err != nil {
				return nil, fmt.Errorf("invalid chat ID %q: %w", idStr, err)
			}
			if id == 0 {
				return nil, fmt.Errorf("invalid chat ID %q: 0 is not a valid Telegram chat", idStr)
			}
			// Duplicate destinations would double-send every SMS.
			if _, dup := seen[id]; dup {
				continue
			}
			seen[id] = struct{}{}
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
		if baudRate <= 0 {
			return nil, fmt.Errorf("invalid BAUD_RATE %q: must be > 0", baudStr)
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

// parseCPIN extracts the exact +CPIN status value.
func parseCPIN(lines []string) (string, bool) {
	for _, line := range lines {
		if strings.HasPrefix(line, "+CPIN:") {
			return strings.ToUpper(strings.TrimSpace(strings.TrimPrefix(line, "+CPIN:"))), true
		}
	}
	return "", false
}

// parseCSQ extracts the RSSI field of a +CSQ response.
func parseCSQ(lines []string) (int, bool) {
	for _, line := range lines {
		if strings.HasPrefix(line, "+CSQ:") {
			fields := strings.Split(strings.TrimPrefix(line, "+CSQ:"), ",")
			if len(fields) >= 1 {
				if rssi, err := strconv.Atoi(strings.TrimSpace(fields[0])); err == nil {
					return rssi, true
				}
			}
		}
	}
	return 0, false
}

// parseCREG extracts the registration status field of a +CREG response.
func parseCREG(lines []string) (int, bool) {
	for _, line := range lines {
		if strings.HasPrefix(line, "+CREG:") {
			fields := strings.Split(strings.TrimPrefix(line, "+CREG:"), ",")
			if len(fields) >= 2 {
				if stat, err := strconv.Atoi(strings.TrimSpace(fields[1])); err == nil {
					return stat, true
				}
			}
		}
	}
	return 0, false
}

// runModemDiagnostics checks modem responsiveness, SIM state, signal and
// network registration. Error kinds are preserved: transport failures return
// a *SessionError (reopen quietly), modem-level problems return a typed
// *DiagnosticError (alert), and cancellation returns ctx.Err().
// networkGrace defines how long after sessionStart unknown signal (CSQ=99)
// and missing registration are tolerated before alerting.
func runModemDiagnostics(ctx context.Context, modem ATCommander, sessionStart time.Time, networkGrace time.Duration) error {
	slog.Info("Testing modem connection...")

	if resp, cmdErr := modem.Command("AT"); cmdErr != nil {
		if IsTimeoutError(cmdErr) {
			return NewSessionError(cmdErr)
		}
		slog.Error("Modem not responding to AT command", "error", cmdErr)
		return NewDiagnosticError(ErrTypeModemNotResponding,
			"Modem not responding to AT commands: %v", cmdErr)
	} else {
		slog.Debug("Modem responds to AT", "response", resp)
	}

	// Modem info is best-effort.
	if resp, err := modem.Command("ATI"); err == nil {
		slog.Info("Modem info", "model", strings.Join(resp, " "))
	}

	// SIM status. The SIM may take a few seconds to initialize after
	// power-on, so a modem ERROR is retried; a transport failure is not a
	// SIM problem and must not be misreported as one.
	slog.Debug("Checking SIM card (may retry if not ready yet)...")
	var cpinStatus string
	for attempt := 1; ; attempt++ {
		resp, err := modem.Command("AT+CPIN?")
		if err == nil {
			status, ok := parseCPIN(resp)
			if !ok {
				return NewDiagnosticError(ErrTypeSimNotDetected,
					"Invalid AT+CPIN? response: %s (expected +CPIN:)", strings.Join(resp, " "))
			}
			cpinStatus = status
			break
		}
		if IsTimeoutError(err) {
			return NewSessionError(err)
		}
		if attempt >= 5 {
			// AT+CPIN? still returns ERROR - check physical presence.
			ccidResp, ccidErr := modem.Command("AT+CCID")
			if ccidErr != nil {
				if IsTimeoutError(ccidErr) {
					return NewSessionError(ccidErr)
				}
				slog.Error("SIM card not detected", "cpin_error", err, "ccid_error", ccidErr)
				return NewDiagnosticError(ErrTypeSimNotDetected,
					"SIM card not physically detected (AT+CPIN? and AT+CCID both fail)")
			}
			slog.Info("SIM card physically detected", "iccid_masked", maskICCID(ccidResp))
			return NewDiagnosticError(ErrTypeSimNotDetected,
				"SIM card detected but not ready (AT+CPIN? fails)")
		}
		slog.Debug("SIM not ready yet, waiting...", "attempt", attempt)
		if !sleepCtx(ctx, 2*time.Second) {
			return ctx.Err()
		}
	}

	slog.Info("SIM status", "status", cpinStatus)
	switch cpinStatus {
	case "READY":
		slog.Info("SIM card is READY")
	case "SIM PIN", "SIM PIN2", "PH-SIM PIN":
		return NewDiagnosticError(ErrTypeSimPinRequired, "SIM card requires PIN code (%s)", cpinStatus)
	case "SIM PUK", "SIM PUK2":
		return NewDiagnosticError(ErrTypeSimPukLocked,
			"SIM card is PUK locked (too many wrong PIN attempts)")
	case "NOT INSERTED":
		return NewDiagnosticError(ErrTypeSimNotDetected, "No SIM card inserted in modem")
	case "NOT READY", "BUSY":
		return NewDiagnosticError(ErrTypeSimNotDetected, "SIM card not ready (still initializing)")
	default:
		return NewDiagnosticError(ErrTypeSimNotDetected, "Unknown SIM status: %s", cpinStatus)
	}

	// Signal and registration share the startup grace window: both CSQ=99
	// and a still-searching CREG are normal for the first seconds after
	// power-on and must not produce an alert/recovery pair on every start.
	checkRadio := func() (rssi, cregStat int, err error) {
		resp, e := modem.Command("AT+CSQ")
		if e != nil {
			if IsTimeoutError(e) {
				return 0, 0, NewSessionError(e)
			}
			return 0, 0, NewDiagnosticError(ErrTypeModemNotResponding, "AT+CSQ failed: %v", e)
		}
		rssi, ok := parseCSQ(resp)
		if !ok {
			return 0, 0, NewDiagnosticError(ErrTypeModemNotResponding,
				"Invalid AT+CSQ response: %s", strings.Join(resp, " "))
		}

		resp, e = modem.Command("AT+CREG?")
		if e != nil {
			if IsTimeoutError(e) {
				return 0, 0, NewSessionError(e)
			}
			return 0, 0, NewDiagnosticError(ErrTypeModemNotResponding, "AT+CREG? failed: %v", e)
		}
		cregStat, ok = parseCREG(resp)
		if !ok {
			return 0, 0, NewDiagnosticError(ErrTypeModemNotResponding,
				"Invalid AT+CREG? response: %s", strings.Join(resp, " "))
		}
		return rssi, cregStat, nil
	}

	waitedForNetwork := false
	for {
		rssi, cregStat, err := checkRadio()
		if err != nil {
			return err
		}
		slog.Info("Radio status", "rssi", rssi, "creg_stat", cregStat)

		if cregStat == 3 {
			slog.Error("NETWORK: Registration denied by operator")
			return NewDiagnosticError(ErrTypeNetworkDenied, "Network operator denied registration")
		}

		registered := cregStat == 1 || cregStat == 5
		signalKnown := rssi != 99
		if registered && signalKnown {
			if rssi == 0 {
				slog.Warn("SIGNAL: Very weak signal (-113 dBm or less)")
			}
			break
		}

		elapsed := clk.Now().Sub(sessionStart)
		if networkGrace <= 0 || elapsed >= networkGrace {
			if !signalKnown {
				slog.Warn("SIGNAL: No signal or not detectable after grace period")
				return NewDiagnosticError(ErrTypeNoSignal, "No signal detected (CSQ=99)")
			}
			slog.Warn("NETWORK: Not registered after grace period", "creg_stat", cregStat)
			return NewDiagnosticError(ErrTypeNetworkNotRegistered,
				"Not registered on network (CREG=%d)", cregStat)
		}

		remaining := networkGrace - elapsed
		wait := 5 * time.Second
		if remaining < wait {
			wait = remaining
		}
		if !waitedForNetwork {
			slog.Info("Waiting for network registration/signal", "grace", networkGrace, "elapsed", elapsed)
			waitedForNetwork = true
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-clk.After(wait):
		}
	}

	// Operator info is best-effort.
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

	// Initialize Telegram bot (unless dry run).
	// The sender is a nil interface in dry-run so nil checks work; a typed-nil
	// *bot.Bot inside the interface would defeat them.
	var sender TelegramSender
	if !cfg.DryRun {
		tgBot, err := bot.New(cfg.TelegramToken, bot.WithSkipGetMe())
		if err != nil {
			return fmt.Errorf("failed to create telegram bot: %w", err)
		}
		sender = tgBot
		slog.Info("Telegram bot initialized")
	} else {
		slog.Warn("Running in DRY_RUN mode - messages will not be sent to Telegram")
	}

	// Create error notifier for sending diagnostic errors to Telegram
	notifier := NewErrorNotifier(sender, cfg.ChatIDs, cfg.DryRun, hostname, cfg.TelegramSendTimeout)

	// The deliverer keeps per-chat cooldowns and the rejected-message set
	// across modem session reopens.
	deliverer := NewDeliverer(sender, notifier, cfg)

	// Retry interval for modem connection issues
	retryInterval := 30 * time.Second
	// Transient session failures retry faster until the alert threshold.
	sessionRetryInterval := 5 * time.Second

	// Track if we need to reset modem on next attempt
	needReset := false

	// A single failed session (timeout, poisoned stream) is reopened quietly;
	// only several consecutive failures mean the modem is really gone.
	consecutiveSessionFailures := 0
	const sessionFailureAlertThreshold = 3
	onHealthy := func() { consecutiveSessionFailures = 0 }

	wait := func(d time.Duration) bool {
		select {
		case <-ctx.Done():
			return false
		case <-clk.After(d):
			return true
		}
	}

	// Main loop with retry logic
	for {
		select {
		case <-ctx.Done():
			slog.Info("Context cancelled, exiting")
			return nil
		default:
		}

		// Try to run the modem polling loop
		err := runModemLoop(ctx, cfg, deliverer, notifier, needReset, onHealthy)

		if err == nil {
			// Normal exit (context cancelled)
			return nil
		}

		// Check if it's a diagnostic error
		var diagErr *DiagnosticError
		if errors.As(err, &diagErr) {
			slog.Error("Modem diagnostic error", "type", errorTypeName(diagErr.Type), "error", diagErr.Message)
			notifier.NotifyError(ctx, diagErr)
			consecutiveSessionFailures = 0

			// Determine if we need a modem reset on the next attempt.
			needReset = needsModemReset(diagErr.Type)
			if needReset {
				slog.Info("Will perform modem reset on next attempt")
			}

			// Wait before retry
			slog.Info("Will retry modem connection", "retry_in", retryInterval)
			if !wait(retryInterval) {
				return nil
			}
			continue
		}

		// Transport/session error: reopen quietly, alert only after repeated failures.
		var sessErr *SessionError
		if errors.As(err, &sessErr) {
			consecutiveSessionFailures++
			slog.Error("Modem session error",
				"error", sessErr.Err,
				"consecutive", consecutiveSessionFailures,
				"alert_threshold", sessionFailureAlertThreshold,
			)
			needReset = false
			if consecutiveSessionFailures >= sessionFailureAlertThreshold {
				notifier.NotifyError(ctx, NewDiagnosticError(ErrTypeModemNotResponding,
					"Modem session failed %d times in a row: %v", consecutiveSessionFailures, sessErr.Err))
				if !wait(retryInterval) {
					return nil
				}
			} else if !wait(sessionRetryInterval) {
				return nil
			}
			continue
		}

		// Non-diagnostic error - log and retry (no reset needed)
		slog.Error("Modem loop error", "error", err)
		needReset = false
		if !wait(retryInterval) {
			return nil
		}
	}
}

// initModemSession performs the mandatory session setup. SMS polling must not
// start unless every command here succeeded: with text mode still active the
// CMGL transcript would be misparsed, with the wrong storage selected the tool
// would inspect (and delete from) the wrong message store, and with delivery
// URCs enabled the modem could interleave +CMT frames into responses.
// Returns the SIM storage usage reported by CPMS (used, total; -1 if unknown).
func initModemSession(modem ATCommander) (simUsed, simTotal int, err error) {
	// Synchronize: absorb boot banners/garbage until the modem answers AT.
	var lastErr error
	for i := 0; i < 3; i++ {
		if _, lastErr = modem.Command("AT"); lastErr == nil {
			break
		}
		if IsTimeoutError(lastErr) {
			return -1, -1, NewSessionError(lastErr)
		}
	}
	if lastErr != nil {
		return -1, -1, NewDiagnosticError(ErrTypeModemNotResponding,
			"Modem not responding during session init: %v", lastErr)
	}

	required := func(cmd string) ([]string, error) {
		resp, cmdErr := modem.Command(cmd)
		if cmdErr == nil {
			return resp, nil
		}
		if IsTimeoutError(cmdErr) {
			return nil, NewSessionError(cmdErr)
		}
		// A modem ERROR on a mandatory SMS command is most often a missing or
		// not-ready SIM (on SIM800 firmware AT+CMGF=0 returns ERROR with no
		// SIM). Probe the SIM so the whole SIM-out episode reports one error
		// type (SIM Not Detected) instead of oscillating into Modem Init
		// Failed, and so it inherits the SIM reset-and-recover path.
		ready, probeErr := simReadyProbe(modem)
		if probeErr != nil {
			return nil, probeErr // transport failure → SessionError
		}
		if !ready {
			return nil, NewDiagnosticError(ErrTypeSimNotDetected,
				"SIM not ready (mandatory init command %s returned ERROR)", cmd)
		}
		return nil, NewDiagnosticError(ErrTypeModemInitFailed,
			"Mandatory init command %s failed: %v", cmd, cmdErr)
	}

	if _, err := required("ATE0"); err != nil {
		return -1, -1, err
	}

	if _, err := required("AT+CMGF=0"); err != nil {
		return -1, -1, err
	}
	// Query the mode back: a modem that silently kept text mode would make the
	// pipeline parse text output as PDUs.
	if resp, err := required("AT+CMGF?"); err != nil {
		return -1, -1, err
	} else if joined := strings.Join(resp, " "); !strings.Contains(joined, "+CMGF: 0") {
		return -1, -1, NewDiagnosticError(ErrTypeModemInitFailed,
			"PDU mode not active after AT+CMGF=0 (got %q)", joined)
	}

	cpmsResp, err := required(`AT+CPMS="SM","SM","SM"`)
	if err != nil {
		return -1, -1, err
	}
	simUsed, simTotal = parseCPMSCounts(cpmsResp)

	// Suppress SMS delivery indications while polling; +CMT/+CMTI frames
	// interleaved into a CMGL transcript are a data-loss hazard.
	if _, cnmiErr := modem.Command("AT+CNMI=2,0,0,0,0"); cnmiErr != nil {
		if IsTimeoutError(cnmiErr) {
			return simUsed, simTotal, NewSessionError(cnmiErr)
		}
		if _, fallbackErr := required("AT+CNMI=0,0,0,0,0"); fallbackErr != nil {
			return simUsed, simTotal, fallbackErr
		}
	}

	slog.Info("Modem session initialized", "sim_used", simUsed, "sim_total", simTotal)
	return simUsed, simTotal, nil
}

// needsModemReset reports whether a diagnostic error type warrants a full
// AT+CFUN reset before the next attempt. SIM-class errors benefit from a
// reset; so does a mandatory-init failure, since a wedged modem (or a
// hot-inserted SIM the modem has not re-read) only recovers via AT+CFUN.
func needsModemReset(t DiagnosticErrorType) bool {
	switch t {
	case ErrTypeSimNotDetected, ErrTypeSimPinRequired, ErrTypeSimPukLocked,
		ErrTypeNetworkDenied, ErrTypeModemInitFailed:
		return true
	default:
		return false
	}
}

// simReadyProbe does a focused SIM readiness check used to reclassify a
// mandatory-init ERROR. Returns (true,nil) only when AT+CPIN? clearly reports
// READY; a transport failure is surfaced as a *SessionError, while a modem
// ERROR (or any non-READY status) counts as "not ready" without erroring.
func simReadyProbe(modem ATCommander) (bool, error) {
	resp, err := modem.Command("AT+CPIN?")
	if err != nil {
		if IsTimeoutError(err) {
			return false, NewSessionError(err)
		}
		return false, nil
	}
	status, ok := parseCPIN(resp)
	return ok && status == "READY", nil
}

// parseCPMSCounts extracts (used, total) of the first storage from a +CPMS
// response line; returns (-1, -1) when the response is unparseable.
func parseCPMSCounts(resp []string) (int, int) {
	for _, line := range resp {
		if !strings.HasPrefix(line, "+CPMS:") {
			continue
		}
		rest := strings.TrimPrefix(line, "+CPMS:")
		var nums []int
		for _, field := range strings.Split(rest, ",") {
			field = strings.TrimSpace(strings.Trim(strings.TrimSpace(field), `"`))
			if n, err := strconv.Atoi(field); err == nil {
				nums = append(nums, n)
			}
		}
		if len(nums) >= 2 {
			return nums[0], nums[1]
		}
	}
	return -1, -1
}

// runModemLoop handles serial port connection and SMS polling.
// needReset indicates if modem should be reset (e.g., after SIM error);
// onHealthy is called once the session is fully initialized and diagnosed.
func runModemLoop(ctx context.Context, cfg *Config, deliverer *Deliverer, notifier *ErrorNotifier, needReset bool, onHealthy func()) error {
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
		if !sleepCtx(ctx, 2*time.Second) {
			return nil
		}
		modem.Command("AT+CFUN=1") // Full functionality (re-init SIM)
		if !sleepCtx(ctx, 5*time.Second) {
			return nil
		}
		slog.Info("Modem reset complete")
	}

	sessionStart := clk.Now()

	// Mandatory session initialization (sync, echo off, PDU mode, SIM storage, CNMI)
	simUsed, simTotal, err := initModemSession(modem)
	if err != nil {
		return err
	}
	notifier.CheckStorage(ctx, simUsed, simTotal)

	// Run detailed modem diagnostics
	slog.Info("Running modem diagnostics...")
	if err := runModemDiagnostics(ctx, modem, sessionStart, cfg.NetworkRegGrace); err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return nil // shutting down: no alert, no recovery
		}
		return err
	}

	// Never announce recovery while shutting down.
	if ctx.Err() != nil {
		return nil
	}

	// Session is fully initialized and diagnosed.
	onHealthy()
	notifier.NotifyRecovery(ctx)

	// Main loop: poll for SMS messages
	pollInterval := 10 * time.Second
	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()

	// Periodic modem health check
	healthCheckInterval := 60 * time.Second
	healthTicker := time.NewTicker(healthCheckInterval)
	defer healthTicker.Stop()

	slog.Info("Starting SMS polling loop",
		"poll_interval", pollInterval,
		"health_check_interval", healthCheckInterval,
	)

	// handleError decides whether an error ends the session. Transport errors
	// end it immediately: after a deadline the response stream cannot be
	// trusted (a late reply would satisfy the wrong command), so the outer
	// loop reopens the port. Repeated-session alerting happens there.
	handleError := func(err error) error {
		slog.Error("Error processing messages", "error", err)

		if IsTimeoutError(err) {
			return NewSessionError(err)
		}

		// Check if it's a modem ERROR response (modem responds but command fails)
		// This often indicates SIM/network issues - run diagnostics immediately
		if IsModemError(err) {
			slog.Warn("Modem returned ERROR - running diagnostics to determine cause")
			// Run diagnostics to get specific error
			if diagErr := runModemDiagnostics(ctx, modem, sessionStart, cfg.NetworkRegGrace); diagErr != nil {
				if errors.Is(diagErr, context.Canceled) || errors.Is(diagErr, context.DeadlineExceeded) {
					return nil
				}
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
	if err := processMessages(ctx, modem, deliverer, cfg, simTotal); err != nil {
		if loopErr := handleError(err); loopErr != nil {
			return loopErr
		}
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
					return NewSessionError(err)
				}
				// Modem responds but with ERROR - run diagnostics
				slog.Warn("Modem health check returned ERROR - running diagnostics")
				if diagErr := runModemDiagnostics(ctx, modem, sessionStart, cfg.NetworkRegGrace); diagErr != nil {
					if errors.Is(diagErr, context.Canceled) || errors.Is(diagErr, context.DeadlineExceeded) {
						return nil
					}
					return diagErr
				}
			}
			slog.Debug("Modem health check passed")
			// Track SIM storage usage so a filling SIM is alerted before
			// inbound SMS start being rejected.
			if resp, cpmsErr := modem.Command("AT+CPMS?"); cpmsErr == nil {
				used, total := parseCPMSCounts(resp)
				notifier.CheckStorage(ctx, used, total)
			}

		case <-ticker.C:
			if err := processMessages(ctx, modem, deliverer, cfg, simTotal); err != nil {
				if loopErr := handleError(err); loopErr != nil {
					return loopErr
				}
			}
		}
	}
}

// sleepCtx waits for d unless the context ends first; returns false on cancellation.
func sleepCtx(ctx context.Context, d time.Duration) bool {
	select {
	case <-ctx.Done():
		return false
	case <-clk.After(d):
		return true
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

// PendingSMS is one deliverable message together with every SIM slot it owns.
// Deletion authority travels with the message: only a fully delivered
// PendingSMS may free exactly its own PartIndices.
type PendingSMS struct {
	Message     SMSMessage
	PartIndices []int
	// RawFallback marks a strictly framed but undecodable PDU forwarded as
	// raw hex (Message.Text holds the PDU, RawReason the parse problem).
	RawFallback bool
	RawReason   string
}

// ListResult is the typed outcome of one CMGL listing.
type ListResult struct {
	Pending              []PendingSMS
	StatusReports        []int    // recognized status reports: deleted without forwarding
	Stale                []int    // multipart parts past MULTIPART_MAX_AGE
	Conflicts            []string // multipart groups with conflicting duplicate parts
	PendingParts         int      // incomplete multipart groups still waiting
	MaxPendingTotalParts int
}

// ErrCMGLCorrupted marks a listing whose header/PDU framing failed
// validation. Nothing from such a transcript may be forwarded or deleted, and
// the session is reopened.
var ErrCMGLCorrupted = errors.New("CMGL transcript corrupted")

// cmglTimeout: a full SIM produces a far larger response than any other
// command we send, so the listing gets its own bounded timeout.
const cmglTimeout = 20 * time.Second

func processMessages(ctx context.Context, modem ATCommander, deliverer *Deliverer, cfg *Config, simTotal int) error {
	slog.Debug("Checking for new SMS messages")

	result, err := listSMSMessages(modem, cfg.MultipartMaxAge)
	if err != nil {
		return fmt.Errorf("failed to list SMS messages: %w", err)
	}

	for _, conflict := range result.Conflicts {
		slog.Warn("Multipart group with conflicting duplicate parts - not assembling", "group", conflict)
	}
	if result.PendingParts > 0 {
		slog.Info("Incomplete multipart messages - waiting for more parts", "pending", result.PendingParts)
	}
	if simTotal > 0 && result.MaxPendingTotalParts > simTotal {
		slog.Warn("Pending multipart message declares more parts than SIM storage can hold - it can never complete",
			"total_parts", result.MaxPendingTotalParts, "sim_capacity", simTotal)
	}

	// Status reports are modem delivery receipts, not user content: delete
	// them without forwarding (documented policy).
	if err := deleteBatch(modem, cfg, result.StatusReports, "status report"); err != nil {
		return err
	}
	// Stale multipart cleanup is independent of delivery success.
	if err := deleteBatch(modem, cfg, result.Stale, "stale multipart part"); err != nil {
		return err
	}

	if len(result.Pending) == 0 {
		slog.Debug("No deliverable messages")
		return nil
	}
	slog.Info("Found SMS messages", "count", len(result.Pending))

	for _, pending := range result.Pending {
		if ctx.Err() != nil {
			return nil
		}

		slog.Debug("Processing SMS",
			"index", pending.Message.Index,
			"from", pending.Message.From,
			"time", pending.Message.Time,
			"text_length", len(pending.Message.Text),
			"raw_fallback", pending.RawFallback,
		)

		switch deliverer.Deliver(ctx, pending) {
		case deliveryDone:
			// Delete exactly this message's slots, immediately after its own
			// successful delivery, so an unrelated later failure can never
			// cause a duplicate of this message.
			if err := deleteBatch(modem, cfg, pending.PartIndices, "forwarded SMS"); err != nil {
				return err
			}
			slog.Info("SMS forwarded successfully",
				"from", pending.Message.From, "indices", pending.PartIndices)

		case deliveryRejected:
			// Permanently rejected: retained on SIM, alerted once, skip it
			// and keep going - one poisoned message must not block the rest.
			continue

		case deliveryDeferred:
			// Transient/rate-limit/config problem: it would hit the next
			// messages too. Stop here; the next poll retries everything
			// still on the SIM.
			slog.Info("Delivery deferred - remaining messages will be retried next poll")
			return nil
		}
	}

	return nil
}

// deleteBatch deletes the given SIM slots. A transport/session error aborts
// immediately (an unacknowledged delete on a desynced stream must not be
// followed by more deletes); a synchronized modem ERROR is logged and skipped.
func deleteBatch(modem ATCommander, cfg *Config, indices []int, kind string) error {
	if len(indices) == 0 {
		return nil
	}
	if cfg.DryRun {
		slog.Info("DRY_RUN: Skipping SMS deletion", "kind", kind, "indices", indices)
		return nil
	}
	for _, idx := range indices {
		slog.Debug("Deleting SMS from SIM", "kind", kind, "index", idx)
		if err := deleteSMS(modem, idx); err != nil {
			if IsTimeoutError(err) {
				return fmt.Errorf("deleting %s at index %d: %w", kind, idx, err)
			}
			slog.Error("Failed to delete SMS (modem ERROR)", "kind", kind, "index", idx, "error", err)
		}
	}
	return nil
}

// cmglRecord is one strictly validated header+PDU pair from a listing.
type cmglRecord struct {
	index  int
	stat   int
	pduHex string
}

func listSMSMessages(modem ATCommander, maxAge time.Duration) (*ListResult, error) {
	// AT+CMGL=4 lists all messages in PDU mode (4 = all)
	resp, err := modem.CommandWithTimeout("AT+CMGL=4", cmglTimeout)
	if err != nil {
		return nil, fmt.Errorf("AT+CMGL command failed: %w", err)
	}

	slog.Debug("CMGL response", "lines", resp)

	records, err := parseCMGLTranscript(resp)
	if err != nil {
		return nil, err
	}

	collector := NewMultipartCollector()
	result := &ListResult{}

	for _, rec := range records {
		// Storage status: 0/1 = received unread/read (ours to forward),
		// 2/3 = stored unsent/sent (not inbound traffic - leave untouched).
		if rec.stat == 2 || rec.stat == 3 {
			slog.Debug("Skipping stored outgoing message", "index", rec.index, "stat", rec.stat)
			continue
		}

		pdu, parseErr := ParsePDU(rec.pduHex)
		if parseErr != nil {
			var notDeliver *NotDeliverError
			var unsupported *UnsupportedEncodingError

			switch {
			case errors.As(parseErr, &notDeliver):
				if notDeliver.MTI == 2 {
					slog.Debug("Status report found", "index", rec.index)
					result.StatusReports = append(result.StatusReports, rec.index)
				} else {
					// A stored SUBMIT under stat 0/1 is not ours to touch.
					slog.Warn("Non-DELIVER PDU in received storage - leaving in place",
						"index", rec.index, "mti", notDeliver.MTI)
				}

			case errors.As(parseErr, &unsupported):
				msg := SMSMessage{Index: rec.index, Text: rec.pduHex}
				if unsupported.Msg != nil {
					msg.From = unsupported.Msg.Sender
					msg.Time = unsupported.Msg.Timestamp
				}
				result.Pending = append(result.Pending, PendingSMS{
					Message:     msg,
					PartIndices: []int{rec.index},
					RawFallback: true,
					RawReason:   parseErr.Error(),
				})

			default: // malformed PDU
				slog.Warn("Failed to parse PDU",
					"index", rec.index,
					"pdu_len", len(rec.pduHex),
					"pdu_fingerprint", contentFingerprint(rec.pduHex),
					"error", parseErr,
				)
				slog.Debug("Unparseable PDU content", "pdu", rec.pduHex)
				result.Pending = append(result.Pending, PendingSMS{
					Message:     SMSMessage{Index: rec.index, Text: rec.pduHex},
					PartIndices: []int{rec.index},
					RawFallback: true,
					RawReason:   parseErr.Error(),
				})
			}
			continue
		}

		if pdu.IsMultipart {
			slog.Debug("Multipart SMS part",
				"index", rec.index,
				"ref", pdu.MultipartRef,
				"part", pdu.PartNumber,
				"total", pdu.TotalParts,
			)
		}

		assembled, partIndices := collector.Add(rec.index, pdu)
		if assembled == nil {
			continue // incomplete or conflicted multipart
		}
		result.Pending = append(result.Pending, PendingSMS{
			Message: SMSMessage{
				Index:       partIndices[0],
				From:        assembled.Sender,
				Text:        assembled.Text,
				Time:        assembled.Timestamp,
				SMSC:        assembled.SMSC,
				IsMultipart: assembled.IsMultipart,
				TotalParts:  assembled.TotalParts,
			},
			PartIndices: partIndices,
		})
	}

	result.PendingParts = collector.Pending()
	result.Conflicts = collector.Conflicts()
	result.MaxPendingTotalParts = collector.MaxPendingTotalParts()
	if maxAge > 0 {
		result.Stale = collector.StaleIndices(maxAge, clk.Now())
		if len(result.Stale) > 0 {
			slog.Warn("Stale multipart parts detected", "count", len(result.Stale), "max_age", maxAge)
		}
	}

	return result, nil
}

// parseCMGLTranscript validates the header/PDU framing of a CMGL response.
// Any inconsistency fails the whole listing: after at.go's URC filtering a
// stray or short line means the transcript cannot be trusted, and forwarding
// or deleting based on it risks losing a real SMS.
func parseCMGLTranscript(lines []string) ([]cmglRecord, error) {
	var records []cmglRecord

	for i := 0; i < len(lines); i++ {
		line := lines[i]
		if !strings.HasPrefix(line, "+CMGL:") {
			return nil, fmt.Errorf("%w: unexpected line %q", ErrCMGLCorrupted, line)
		}

		index, stat, tpduLen, err := parseCMGLHeader(line)
		if err != nil {
			return nil, fmt.Errorf("%w: %v", ErrCMGLCorrupted, err)
		}

		if i+1 >= len(lines) {
			return nil, fmt.Errorf("%w: header %q without PDU line", ErrCMGLCorrupted, line)
		}
		i++
		pduStr := strings.TrimSpace(lines[i])

		if err := validatePDULine(pduStr, tpduLen); err != nil {
			return nil, fmt.Errorf("%w: index %d: %v", ErrCMGLCorrupted, index, err)
		}

		records = append(records, cmglRecord{index: index, stat: stat, pduHex: pduStr})
	}

	return records, nil
}

// parseCMGLHeader parses "+CMGL: <index>,<stat>,<alpha>,<length>" honoring
// quoted alpha fields that may contain commas.
func parseCMGLHeader(line string) (index, stat, tpduLen int, err error) {
	rest := strings.TrimSpace(strings.TrimPrefix(line, "+CMGL:"))
	fields := splitQuoted(rest)
	if len(fields) < 3 {
		return 0, 0, 0, fmt.Errorf("header %q has %d fields, want >= 3", line, len(fields))
	}

	index, err = strconv.Atoi(strings.TrimSpace(fields[0]))
	if err != nil {
		return 0, 0, 0, fmt.Errorf("bad index in header %q: %v", line, err)
	}
	// Text-mode listings quote the status ("REC UNREAD"); seeing one means
	// PDU mode is not active and nothing in the transcript can be trusted.
	stat, err = strconv.Atoi(strings.TrimSpace(fields[1]))
	if err != nil {
		return 0, 0, 0, fmt.Errorf("bad stat in header %q: %v", line, err)
	}
	tpduLen, err = strconv.Atoi(strings.TrimSpace(fields[len(fields)-1]))
	if err != nil {
		return 0, 0, 0, fmt.Errorf("bad length in header %q: %v", line, err)
	}
	return index, stat, tpduLen, nil
}

// splitQuoted splits a comma-separated field list, keeping quoted fields
// (which may contain commas) intact.
func splitQuoted(s string) []string {
	var fields []string
	var cur strings.Builder
	inQuotes := false
	for _, r := range s {
		switch {
		case r == '"':
			inQuotes = !inQuotes
		case r == ',' && !inQuotes:
			fields = append(fields, cur.String())
			cur.Reset()
		default:
			cur.WriteRune(r)
		}
	}
	fields = append(fields, cur.String())
	return fields
}

// validatePDULine checks that the candidate PDU line is pure hex and its
// byte count matches the header: total = 1 (SMSC length octet) + SMSC bytes
// + <length> TPDU bytes. This is what proves the line really is the PDU
// belonging to the preceding header.
func validatePDULine(pduStr string, tpduLen int) error {
	if pduStr == "" {
		return fmt.Errorf("empty PDU line")
	}
	if len(pduStr)%2 != 0 {
		return fmt.Errorf("odd hex length %d", len(pduStr))
	}
	data, err := hex.DecodeString(pduStr)
	if err != nil {
		return fmt.Errorf("not hex: %v", err)
	}
	smscLen := int(data[0])
	expected := 1 + smscLen + tpduLen
	if len(data) != expected {
		return fmt.Errorf("PDU is %d bytes, header requires %d (SMSC %d + TPDU %d)",
			len(data), expected, smscLen, tpduLen)
	}
	return nil
}

func deleteSMS(modem ATCommander, index int) error {
	cmd := fmt.Sprintf("AT+CMGD=%d", index)
	_, err := modem.Command(cmd)
	return err
}

// formatTelegramMessage renders a single (non-chunked) SMS notification.
func formatTelegramMessage(msg SMSMessage) string {
	return buildTelegramMessages(PendingSMS{Message: msg})[0]
}

func escapeHTML(s string) string {
	replacer := strings.NewReplacer(
		"&", "&amp;",
		"<", "&lt;",
		">", "&gt;",
	)
	return replacer.Replace(s)
}
