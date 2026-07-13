// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

//go:build live

// Live loopback suite: sends real SMS to the modem's own number through the
// real network and verifies reception, decoding, Telegram delivery and SIM
// cleanup end-to-end.
//
// Never runs in CI (build tag `live` + explicit env). Requirements:
//   - exclusive access to the modem (stop the systemd service first);
//   - a SIM able to receive self-addressed SMS, PIN disabled;
//   - each run sends real, billed SMS (multipart scenario sends three).
//
// Safety: only messages carrying this run's nonce are ever delivered or
// deleted; foreign SMS on the SIM are left untouched. Prefer a dedicated test
// SIM anyway.
//
// Run:
//
//	source tokens.sh   # or export TELEGRAM_BOT_TOKEN
//	LIVE_SERIAL_PORT=/dev/ttyUSB0 \
//	LIVE_SELF_NUMBER=+<your SIM's own number> \
//	LIVE_TELEGRAM_CHAT_ID=<dedicated test chat id> \
//	go test -tags live -run TestLive -v -timeout 20m .
package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/go-telegram/bot"
	"github.com/go-telegram/bot/models"
	"github.com/tarm/serial"
)

const (
	liveDeliveryTimeout = 3 * time.Minute
	livePollInterval    = 5 * time.Second
	liveSendTimeout     = 60 * time.Second // network submission after Ctrl+Z is slow
)

// recordingSender forwards to the real Telegram bot and records what was sent.
type recordingSender struct {
	mu   sync.Mutex
	real TelegramSender
	sent []sentMessage
}

func (r *recordingSender) SendMessage(ctx context.Context, params *bot.SendMessageParams) (*models.Message, error) {
	msg, err := r.real.SendMessage(ctx, params)
	if err == nil {
		r.mu.Lock()
		chatID, _ := params.ChatID.(int64)
		r.sent = append(r.sent, sentMessage{ChatID: chatID, Text: params.Text})
		r.mu.Unlock()
	}
	return msg, err
}

type liveHarness struct {
	t          *testing.T
	modem      *SimpleAT
	cfg        *Config
	deliverer  *Deliverer
	recorder   *recordingSender
	selfNumber string
	nonce      string
}

// setupLive skips the test unless the live environment is fully configured,
// then opens the modem session and prepares real Telegram delivery.
func setupLive(t *testing.T) *liveHarness {
	t.Helper()

	portPath := os.Getenv("LIVE_SERIAL_PORT")
	selfNumber := os.Getenv("LIVE_SELF_NUMBER")
	token := os.Getenv("TELEGRAM_BOT_TOKEN")
	chatIDStr := os.Getenv("LIVE_TELEGRAM_CHAT_ID")
	if portPath == "" || selfNumber == "" || token == "" || chatIDStr == "" {
		t.Skip("live env not configured: need LIVE_SERIAL_PORT, LIVE_SELF_NUMBER, TELEGRAM_BOT_TOKEN, LIVE_TELEGRAM_CHAT_ID")
	}
	if !strings.HasPrefix(selfNumber, "+") {
		t.Fatalf("LIVE_SELF_NUMBER must be international (+...), got %q", selfNumber)
	}
	chatID, err := strconv.ParseInt(chatIDStr, 10, 64)
	if err != nil || chatID == 0 {
		t.Fatalf("invalid LIVE_TELEGRAM_CHAT_ID %q", chatIDStr)
	}
	baud := 115200
	if s := os.Getenv("LIVE_BAUD_RATE"); s != "" {
		if baud, err = strconv.Atoi(s); err != nil {
			t.Fatalf("invalid LIVE_BAUD_RATE %q", s)
		}
	}

	p, err := serial.OpenPort(&serial.Config{Name: portPath, Baud: baud, ReadTimeout: 500 * time.Millisecond})
	if err != nil {
		t.Fatalf("open serial port (is the sms-to-telegram service stopped?): %v", err)
	}
	t.Cleanup(func() { p.Close() })

	modem := NewSimpleAT(p, 5*time.Second)
	if _, _, err := initModemSession(modem); err != nil {
		t.Fatalf("initModemSession: %v", err)
	}
	if err := runModemDiagnostics(context.Background(), modem, clk.Now(), 90*time.Second); err != nil {
		t.Fatalf("modem diagnostics: %v", err)
	}

	tgBot, err := bot.New(token, bot.WithSkipGetMe())
	if err != nil {
		t.Fatalf("bot.New: %v", err)
	}
	recorder := &recordingSender{real: tgBot}
	cfg := &Config{ChatIDs: []int64{chatID}, TelegramSendTimeout: 30 * time.Second}
	// Notifier in dry-run: harness failures go to the test log, not the chat.
	notifier := NewErrorNotifier(nil, cfg.ChatIDs, true, "live-test", 30*time.Second)

	h := &liveHarness{
		t:          t,
		modem:      modem,
		cfg:        cfg,
		deliverer:  NewDeliverer(recorder, notifier, cfg),
		recorder:   recorder,
		selfNumber: selfNumber,
		nonce:      fmt.Sprintf("LIVE-%d", time.Now().UnixNano()%1_000_000_000),
	}
	t.Cleanup(h.cleanup)
	return h
}

// sendSelfSMS submits one PDU to the modem's own number and asserts the
// network accepted it.
func (h *liveHarness) sendSelfSMS(body string, ucs2 bool, concat *concatRef) {
	h.t.Helper()
	pduHex, tpduLen, err := encodeSubmitPDU(h.selfNumber, body, ucs2, concat)
	if err != nil {
		h.t.Fatalf("encodeSubmitPDU: %v", err)
	}
	resp, err := h.modem.CommandWithPrompt(fmt.Sprintf("AT+CMGS=%d", tpduLen), pduHex, liveSendTimeout)
	if err != nil {
		h.t.Fatalf("AT+CMGS failed: %v", err)
	}
	h.t.Logf("submitted SMS (%d TPDU bytes): %s", tpduLen, strings.Join(resp, " "))
}

// waitForNonce polls the SIM until a deliverable message carrying this run's
// nonce appears (multipart is returned only once fully assembled).
func (h *liveHarness) waitForNonce() PendingSMS {
	h.t.Helper()
	deadline := time.Now().Add(liveDeliveryTimeout)
	for time.Now().Before(deadline) {
		result, err := listSMSMessages(h.modem, 0)
		if err != nil {
			h.t.Fatalf("listSMSMessages: %v", err)
		}
		for _, pending := range result.Pending {
			if !pending.RawFallback && strings.Contains(pending.Message.Text, h.nonce) {
				return pending
			}
			if pending.RawFallback && strings.Contains(pending.Message.Text, h.nonce) {
				h.t.Fatalf("nonce message came back undecodable: %s", pending.RawReason)
			}
		}
		if result.PendingParts > 0 {
			h.t.Logf("waiting: %d incomplete multipart group(s) on SIM", result.PendingParts)
		}
		time.Sleep(livePollInterval)
	}
	h.t.Fatalf("SMS with nonce %s not received within %v", h.nonce, liveDeliveryTimeout)
	return PendingSMS{}
}

// deliverAndVerify pushes the message through the real Deliverer, deletes its
// slots and verifies they are gone from the SIM.
func (h *liveHarness) deliverAndVerify(pending PendingSMS) {
	h.t.Helper()

	status := h.deliverer.Deliver(context.Background(), pending)
	if status != deliveryDone {
		h.t.Fatalf("Deliver() = %v, want deliveryDone", status)
	}
	found := false
	for _, m := range h.recorder.sent {
		if strings.Contains(m.Text, h.nonce) {
			found = true
			break
		}
	}
	if !found {
		h.t.Fatal("Telegram accepted nothing containing the nonce")
	}

	if err := deleteBatch(h.modem, h.cfg, pending.PartIndices, "live test SMS"); err != nil {
		h.t.Fatalf("deleteBatch: %v", err)
	}

	result, err := listSMSMessages(h.modem, 0)
	if err != nil {
		h.t.Fatalf("listSMSMessages after delete: %v", err)
	}
	for _, p := range result.Pending {
		if strings.Contains(p.Message.Text, h.nonce) {
			h.t.Fatalf("nonce message still on SIM after deletion (indices %v)", p.PartIndices)
		}
	}
}

// cleanup removes any leftover slots carrying this run's nonce, including
// stranded single parts of an incomplete multipart (best effort).
func (h *liveHarness) cleanup() {
	if h.modem.Poisoned() {
		h.t.Log("cleanup skipped: modem session poisoned")
		return
	}
	resp, err := h.modem.CommandWithTimeout("AT+CMGL=4", cmglTimeout)
	if err != nil {
		h.t.Logf("cleanup: CMGL failed: %v", err)
		return
	}
	records, err := parseCMGLTranscript(resp)
	if err != nil {
		h.t.Logf("cleanup: %v", err)
		return
	}
	for _, rec := range records {
		pdu, parseErr := ParsePDU(rec.pduHex)
		if parseErr != nil || !strings.Contains(pdu.Text, h.nonce) {
			continue
		}
		h.t.Logf("cleanup: deleting leftover nonce slot %d", rec.index)
		if err := deleteSMS(h.modem, rec.index); err != nil {
			h.t.Logf("cleanup: delete %d failed: %v", rec.index, err)
		}
	}
}

// --- Scenarios -----------------------------------------------------------------

// TestLive_GSM7RoundTrip: plain GSM7 SMS through the real network, byte-exact
// text round trip, real Telegram delivery, SIM slot freed.
func TestLive_GSM7RoundTrip(t *testing.T) {
	h := setupLive(t)
	body := h.nonce + " gsm7 round trip"

	h.sendSelfSMS(body, false, nil)
	pending := h.waitForNonce()

	if pending.Message.Text != body {
		t.Errorf("round trip text = %q, want %q", pending.Message.Text, body)
	}
	// Sender formatting varies by operator; compare on the number tail only.
	digits := strings.TrimPrefix(h.selfNumber, "+")
	if tail := digits[max(0, len(digits)-4):]; !strings.Contains(pending.Message.From, tail) {
		t.Logf("note: sender %q does not obviously match self number %q (operator formatting)", pending.Message.From, h.selfNumber)
	}
	h.deliverAndVerify(pending)
}

// TestLive_UCS2Emoji: Cyrillic + emoji (surrogate pair) through the real SMSC.
func TestLive_UCS2Emoji(t *testing.T) {
	h := setupLive(t)
	body := h.nonce + " Привет 😀 тест"

	h.sendSelfSMS(body, true, nil)
	pending := h.waitForNonce()

	if pending.Message.Text != body {
		t.Errorf("round trip text = %q, want %q", pending.Message.Text, body)
	}
	h.deliverAndVerify(pending)
}

// TestLive_MultipartAssembly: three concatenated parts must be reassembled in
// order, delivered as one message, and all three SIM slots freed.
func TestLive_MultipartAssembly(t *testing.T) {
	h := setupLive(t)
	ref := int(time.Now().Unix() % 256)
	parts := []string{
		h.nonce + " part one. ",
		h.nonce + " part two. ",
		h.nonce + " part three.",
	}

	for i, part := range parts {
		h.sendSelfSMS(part, false, &concatRef{ref: ref, total: len(parts), part: i + 1})
	}

	pending := h.waitForNonce()
	want := strings.Join(parts, "")
	if pending.Message.Text != want {
		t.Errorf("assembled text = %q, want %q", pending.Message.Text, want)
	}
	if len(pending.PartIndices) != len(parts) {
		t.Errorf("PartIndices = %v, want %d slots", pending.PartIndices, len(parts))
	}
	if !pending.Message.IsMultipart || pending.Message.TotalParts != len(parts) {
		t.Errorf("multipart metadata = %+v", pending.Message)
	}
	h.deliverAndVerify(pending)
}

// TestLive_GSM7FinnishChars: national characters of the GSM7 default alphabet
// (realistic Finnish traffic) must survive the operator's SMSC unchanged.
func TestLive_GSM7FinnishChars(t *testing.T) {
	h := setupLive(t)
	body := h.nonce + " äöå ÄÖÅ éü ñ"

	h.sendSelfSMS(body, false, nil)
	pending := h.waitForNonce()

	if pending.Message.Text != body {
		t.Errorf("round trip text = %q, want %q", pending.Message.Text, body)
	}
	h.deliverAndVerify(pending)
}

// setupLiveModem is the lightweight variant of setupLive for tests that only
// need the modem (no Telegram, no SMS cost): it requires just LIVE_SERIAL_PORT.
func setupLiveModem(t *testing.T) *SimpleAT {
	t.Helper()

	portPath := os.Getenv("LIVE_SERIAL_PORT")
	if portPath == "" {
		t.Skip("live env not configured: need LIVE_SERIAL_PORT")
	}
	baud := 115200
	if s := os.Getenv("LIVE_BAUD_RATE"); s != "" {
		var err error
		if baud, err = strconv.Atoi(s); err != nil {
			t.Fatalf("invalid LIVE_BAUD_RATE %q", s)
		}
	}

	p, err := serial.OpenPort(&serial.Config{Name: portPath, Baud: baud, ReadTimeout: 500 * time.Millisecond})
	if err != nil {
		t.Fatalf("open serial port (is the sms-to-telegram service stopped?): %v", err)
	}
	t.Cleanup(func() { p.Close() })

	modem := NewSimpleAT(p, 5*time.Second)
	if _, _, err := initModemSession(modem); err != nil {
		t.Fatalf("initModemSession: %v", err)
	}
	return modem
}

// TestLive_FlightModeRadioRecovery drives a real radio outage without any RF
// shielding: AT+CFUN=4 (flight mode) makes the modem genuinely report a lost
// network, diagnostics must classify it as a radio-group error, and the same
// AT+CFUN cycle the reset escalation performs must bring the radio back to a
// passing diagnosis. Costs no SMS and needs no Telegram configuration.
func TestLive_FlightModeRadioRecovery(t *testing.T) {
	modem := setupLiveModem(t)

	// Whatever happens, never leave the modem in flight mode. Note: on SIM800
	// R14.18 the CFUN setting SURVIVES a power cycle, so an aborted run would
	// otherwise leave the modem radio-dead until someone sends CFUN=1.
	t.Cleanup(func() {
		if modem.Poisoned() {
			t.Log("cleanup: session poisoned, cannot restore CFUN=1")
			return
		}
		modem.CommandWithTimeout("AT+CFUN=1", 20*time.Second)
	})

	// Precondition: force full functionality, healing any flight mode left
	// over from a previously aborted run (see the cleanup note above).
	if _, err := modem.CommandWithTimeout("AT+CFUN=1", 20*time.Second); err != nil {
		t.Fatalf("AT+CFUN=1 precondition: %v", err)
	}
	time.Sleep(2 * time.Second)

	// Baseline: radio healthy.
	if err := runModemDiagnostics(context.Background(), modem, clk.Now(), 90*time.Second); err != nil {
		t.Fatalf("baseline diagnostics: %v", err)
	}

	// Radio off. The modem now truthfully reports no service; with a short
	// grace the diagnostics must return a radio-group error.
	if _, err := modem.CommandWithTimeout("AT+CFUN=4", 15*time.Second); err != nil {
		t.Fatalf("AT+CFUN=4: %v", err)
	}
	time.Sleep(2 * time.Second)

	err := runModemDiagnostics(context.Background(), modem, clk.Now(), 15*time.Second)
	var diagErr *DiagnosticError
	if !errors.As(err, &diagErr) {
		t.Fatalf("diagnostics with radio off: err = %v, want DiagnosticError", err)
	}
	if alertGroup(diagErr.Type) != alertGroup(ErrTypeNoSignal) {
		t.Fatalf("radio-off verdict = %s, want the radio group (No Signal / Not Registered)",
			errorTypeName(diagErr.Type))
	}
	t.Logf("radio-off verdict: %s (%s)", errorTypeName(diagErr.Type), diagErr.Message)

	// The cure applied by the reset escalation: a CFUN cycle back to full
	// functionality. Diagnostics must pass again within a registration grace.
	if _, err := modem.CommandWithTimeout("AT+CFUN=1", 20*time.Second); err != nil {
		t.Fatalf("AT+CFUN=1: %v", err)
	}
	time.Sleep(2 * time.Second)
	if err := runModemDiagnostics(context.Background(), modem, clk.Now(), 90*time.Second); err != nil {
		t.Fatalf("diagnostics after radio restore: %v", err)
	}
	t.Log("radio restored and re-registered")
}
