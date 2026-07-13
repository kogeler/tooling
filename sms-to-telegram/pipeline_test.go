// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/go-telegram/bot"
)

// Real captured single-part UCS2 PDU decoding to "Тест1" (37 bytes → TPDU 29).
const testPDUSingle = "0791534874894370000C915348948470870008522111218305800A04220435044104420031"

func testConfig() *Config {
	return &Config{
		ChatIDs:             []int64{100, 200},
		TelegramSendTimeout: time.Second,
	}
}

// newTestDeliverer wires a Deliverer with independent fakes for SMS delivery
// and operator alerts.
func newTestDeliverer(cfg *Config) (*Deliverer, *fakeSender, *fakeSender) {
	sender := &fakeSender{}
	alertSender := &fakeSender{}
	notifier := NewErrorNotifier(alertSender, cfg.ChatIDs, false, "test-host", time.Second)
	return NewDeliverer(sender, notifier, cfg), sender, alertSender
}

func cmglListing(entries ...[2]string) []string {
	var lines []string
	for _, e := range entries {
		lines = append(lines, e[0], e[1])
	}
	return lines
}

// TestProcessMessages_SuccessDeletes: happy path — one SMS is forwarded to all
// chats and its SIM slot is deleted afterwards.
func TestProcessMessages_SuccessDeletes(t *testing.T) {
	t.Cleanup(swapClock(newFakeClock()))
	at := newFakeAT()
	at.on("AT+CMGL=4", cmglListing([2]string{"+CMGL: 5,1,,29", testPDUSingle}), nil)
	cfg := testConfig()
	deliverer, sender, _ := newTestDeliverer(cfg)

	if err := processMessages(context.Background(), at, deliverer, cfg, 30); err != nil {
		t.Fatalf("processMessages() error = %v", err)
	}

	if got := len(sender.sentTo(100)); got != 1 {
		t.Errorf("chat 100 received %d messages, want 1", got)
	}
	if got := len(sender.sentTo(200)); got != 1 {
		t.Errorf("chat 200 received %d messages, want 1", got)
	}
	if n := at.commandCount("AT+CMGD=5"); n != 1 {
		t.Errorf("AT+CMGD=5 called %d times, want 1", n)
	}
}

// TestProcessMessages_TransientFailureRetains: the core no-loss invariant —
// if delivery fails, the SIM slot must NOT be deleted; the poll cycle retries.
func TestProcessMessages_TransientFailureRetains(t *testing.T) {
	t.Cleanup(swapClock(newFakeClock()))
	at := newFakeAT()
	at.on("AT+CMGL=4", cmglListing([2]string{"+CMGL: 5,1,,29", testPDUSingle}), nil)
	cfg := testConfig()
	deliverer, sender, _ := newTestDeliverer(cfg)
	sender.script = func(_ int, _ int64, _ string) error {
		return errors.New("network down")
	}

	if err := processMessages(context.Background(), at, deliverer, cfg, 30); err != nil {
		t.Fatalf("processMessages() error = %v (deferred delivery is not a loop error)", err)
	}
	if n := at.commandCount("AT+CMGD=5"); n != 0 {
		t.Errorf("AT+CMGD=5 called %d times after failed delivery, want 0", n)
	}
}

// TestProcessMessages_DryRun: DRY_RUN must neither send nor delete.
func TestProcessMessages_DryRun(t *testing.T) {
	t.Cleanup(swapClock(newFakeClock()))
	at := newFakeAT()
	at.on("AT+CMGL=4", cmglListing([2]string{"+CMGL: 5,1,,29", testPDUSingle}), nil)
	cfg := testConfig()
	cfg.DryRun = true
	deliverer, sender, _ := newTestDeliverer(cfg)

	if err := processMessages(context.Background(), at, deliverer, cfg, 30); err != nil {
		t.Fatalf("processMessages() error = %v", err)
	}
	if len(sender.sent) != 0 {
		t.Errorf("DRY_RUN sent %d messages", len(sender.sent))
	}
	for _, call := range at.calls {
		if strings.HasPrefix(call, "AT+CMGD=") {
			t.Errorf("DRY_RUN issued deletion command %q", call)
		}
	}
}

// TestProcessMessages_MidBatchRejectionContinues: a permanently rejected
// message is retained and alerted once, while other messages still flow.
func TestProcessMessages_MidBatchRejectionContinues(t *testing.T) {
	t.Cleanup(swapClock(newFakeClock()))
	at := newFakeAT()
	listing := cmglListing(
		[2]string{"+CMGL: 1,1,,29", testPDUSingle},
		[2]string{"+CMGL: 2,1,,24", pduAlphaSender},
	)
	at.on("AT+CMGL=4", listing, nil)
	at.on("AT+CMGL=4", listing, nil) // second poll sees the retained message
	cfg := testConfig()
	cfg.ChatIDs = []int64{100}
	deliverer, sender, alertSender := newTestDeliverer(cfg)
	// Reject everything containing "Тест1" (message at index 1), including
	// its plain-text fallback; deliver the rest.
	sender.script = func(_ int, _ int64, text string) error {
		if strings.Contains(text, "Тест1") {
			return fmt.Errorf("%w, message is too weird", bot.ErrorBadRequest)
		}
		return nil
	}

	if err := processMessages(context.Background(), at, deliverer, cfg, 30); err != nil {
		t.Fatalf("processMessages() error = %v", err)
	}

	if n := at.commandCount("AT+CMGD=1"); n != 0 {
		t.Error("rejected message must not be deleted")
	}
	if n := at.commandCount("AT+CMGD=2"); n != 1 {
		t.Errorf("later message deleted %d times, want 1 (rejection must not block it)", n)
	}
	if len(alertSender.sent) != 1 {
		t.Errorf("rejection alerts = %d, want exactly 1", len(alertSender.sent))
	}

	// Second poll: the rejected message is skipped silently (no resend, no
	// duplicate alert), the already-forwarded one is gone from the SIM.
	sentBefore := len(sender.sent)
	if err := processMessages(context.Background(), at, deliverer, cfg, 30); err != nil {
		t.Fatalf("second poll error = %v", err)
	}
	rejectedResends := 0
	for _, m := range sender.sent[sentBefore:] {
		if strings.Contains(m.Text, "Тест1") {
			rejectedResends++
		}
	}
	if rejectedResends != 0 {
		t.Errorf("rejected message re-sent %d times on second poll", rejectedResends)
	}
	if len(alertSender.sent) != 1 {
		t.Errorf("alerts after second poll = %d, want still 1", len(alertSender.sent))
	}
}

// TestProcessMessages_TransientStopsBatch: a transient failure defers the
// whole rest of the batch (it would fail too); nothing is deleted.
func TestProcessMessages_TransientStopsBatch(t *testing.T) {
	t.Cleanup(swapClock(newFakeClock()))
	at := newFakeAT()
	at.on("AT+CMGL=4", cmglListing(
		[2]string{"+CMGL: 1,1,,29", testPDUSingle},
		[2]string{"+CMGL: 2,1,,24", pduAlphaSender},
	), nil)
	cfg := testConfig()
	cfg.ChatIDs = []int64{100}
	deliverer, sender, _ := newTestDeliverer(cfg)
	sender.script = func(_ int, _ int64, _ string) error {
		return errors.New("network down")
	}

	if err := processMessages(context.Background(), at, deliverer, cfg, 30); err != nil {
		t.Fatalf("processMessages() error = %v", err)
	}
	for _, call := range at.calls {
		if strings.HasPrefix(call, "AT+CMGD=") {
			t.Errorf("deletion %q issued despite transient failure", call)
		}
	}
	// Only the first message was attempted (3 attempts); the second was not.
	for _, m := range sender.sent {
		if strings.Contains(m.Text, "Hello") {
			t.Error("second message attempted during transient outage")
		}
	}
}

// TestProcessMessages_RateLimitCooldown: 429 honors retry_after — delivery is
// deferred while the chat cools down and succeeds afterwards.
func TestProcessMessages_RateLimitCooldown(t *testing.T) {
	fc := newFakeClock()
	t.Cleanup(swapClock(fc))
	at := newFakeAT()
	listing := cmglListing([2]string{"+CMGL: 5,1,,29", testPDUSingle})
	at.on("AT+CMGL=4", listing, nil)
	at.on("AT+CMGL=4", listing, nil)
	at.on("AT+CMGL=4", listing, nil)
	cfg := testConfig()
	cfg.ChatIDs = []int64{100}
	deliverer, sender, _ := newTestDeliverer(cfg)
	first := true
	sender.script = func(_ int, _ int64, _ string) error {
		if first {
			first = false
			return &bot.TooManyRequestsError{Message: "too many requests", RetryAfter: 30}
		}
		return nil
	}

	// First poll: hit the rate limit → retained.
	if err := processMessages(context.Background(), at, deliverer, cfg, 30); err != nil {
		t.Fatalf("poll 1 error = %v", err)
	}
	if at.commandCount("AT+CMGD=5") != 0 {
		t.Fatal("deleted while rate-limited")
	}

	// Second poll while still cooling down: no send attempt at all.
	sentBefore := len(sender.sent)
	if err := processMessages(context.Background(), at, deliverer, cfg, 30); err != nil {
		t.Fatalf("poll 2 error = %v", err)
	}
	if len(sender.sent) != sentBefore {
		t.Error("send attempted during cooldown")
	}

	// After the cooldown expires the message goes through and is deleted.
	fc.Advance(31 * time.Second)
	if err := processMessages(context.Background(), at, deliverer, cfg, 30); err != nil {
		t.Fatalf("poll 3 error = %v", err)
	}
	if at.commandCount("AT+CMGD=5") != 1 {
		t.Error("message not delivered/deleted after cooldown expired")
	}
}

// TestProcessMessages_StatusReportDeletedSilently: recognized status reports
// are deleted without forwarding.
func TestProcessMessages_StatusReportDeletedSilently(t *testing.T) {
	t.Cleanup(swapClock(newFakeClock()))
	at := newFakeAT()
	at.on("AT+CMGL=4", cmglListing([2]string{"+CMGL: 9,1,,26", pduStatusReport}), nil)
	cfg := testConfig()
	deliverer, sender, _ := newTestDeliverer(cfg)

	if err := processMessages(context.Background(), at, deliverer, cfg, 30); err != nil {
		t.Fatalf("processMessages() error = %v", err)
	}
	if len(sender.sent) != 0 {
		t.Errorf("status report was forwarded (%d sends)", len(sender.sent))
	}
	if at.commandCount("AT+CMGD=9") != 1 {
		t.Error("status report slot not deleted")
	}
}

// TestProcessMessages_StoredOutgoingRetained: stat 2/3 (stored unsent/sent)
// entries are not inbound traffic — never forwarded, never deleted.
func TestProcessMessages_StoredOutgoingRetained(t *testing.T) {
	t.Cleanup(swapClock(newFakeClock()))
	at := newFakeAT()
	at.on("AT+CMGL=4", cmglListing([2]string{"+CMGL: 7,3,,29", testPDUSingle}), nil)
	cfg := testConfig()
	deliverer, sender, _ := newTestDeliverer(cfg)

	if err := processMessages(context.Background(), at, deliverer, cfg, 30); err != nil {
		t.Fatalf("processMessages() error = %v", err)
	}
	if len(sender.sent) != 0 {
		t.Error("stored outgoing message was forwarded")
	}
	if at.commandCount("AT+CMGD=7") != 0 {
		t.Error("stored outgoing message was deleted")
	}
}

// TestProcessMessages_CorruptedTranscript: framing violations abort the whole
// listing with no sends and no deletes.
func TestProcessMessages_CorruptedTranscript(t *testing.T) {
	t.Cleanup(swapClock(newFakeClock()))
	corrupt := [][]string{
		{"+CMGL: 5,1,,30", "+CMTI: \"SM\",7"},                    // URC line instead of PDU
		{"+CMGL: 5,1,,24", testPDUSingle},                        // length mismatch with header
		{"+CMGL: 5,1,,29", testPDUSingle[:len(testPDUSingle)-4]}, // truncated PDU
		{"+CMGL: 5,1,,30"},                                       // header without PDU line
		{"stray garbage line"},                                   // no header at all
		{"+CMGL: 5,\"REC UNREAD\",,29", testPDUSingle},           // text-mode listing
	}
	for i, lines := range corrupt {
		at := newFakeAT()
		at.on("AT+CMGL=4", lines, nil)
		cfg := testConfig()
		deliverer, sender, _ := newTestDeliverer(cfg)

		err := processMessages(context.Background(), at, deliverer, cfg, 30)
		if !errors.Is(err, ErrCMGLCorrupted) {
			t.Errorf("case %d: error = %v, want ErrCMGLCorrupted", i, err)
		}
		if len(sender.sent) != 0 {
			t.Errorf("case %d: sent %d messages from corrupted transcript", i, len(sender.sent))
		}
		for _, call := range at.calls {
			if strings.HasPrefix(call, "AT+CMGD=") {
				t.Errorf("case %d: deletion %q from corrupted transcript", i, call)
			}
		}
	}
}

// TestProcessMessages_MultipartOwnsAllSlots: an assembled multipart deletes
// all and only its own part slots after delivery.
func TestProcessMessages_MultipartOwnsAllSlots(t *testing.T) {
	t.Cleanup(swapClock(newFakeClock()))
	at := newFakeAT()
	at.on("AT+CMGL=4", cmglListing(
		[2]string{"+CMGL: 3,1,,30", pduGSM7Part1},
		[2]string{"+CMGL: 4,1,,30", pduGSM7Part2},
	), nil)
	cfg := testConfig()
	cfg.ChatIDs = []int64{100}
	deliverer, sender, _ := newTestDeliverer(cfg)

	if err := processMessages(context.Background(), at, deliverer, cfg, 30); err != nil {
		t.Fatalf("processMessages() error = %v", err)
	}
	sent := sender.sentTo(100)
	if len(sent) != 1 || !strings.Contains(sent[0].Text, "HelloWorld") {
		t.Fatalf("sent = %+v, want one assembled HelloWorld message", sent)
	}
	if at.commandCount("AT+CMGD=3") != 1 || at.commandCount("AT+CMGD=4") != 1 {
		t.Error("both multipart slots must be deleted after delivery")
	}
}

// TestProcessMessages_UndecodablePDUForwardedAsRaw: a strictly framed but
// unparseable PDU is forwarded as marked raw hex, then deleted.
func TestProcessMessages_UndecodablePDUForwardedAsRaw(t *testing.T) {
	t.Cleanup(swapClock(newFakeClock()))
	// Structurally invalid TPDU (too short), but framed correctly:
	// 9 bytes total, SMSC len 0 → TPDU length 8.
	rawPDU := "000401AA110000FF22"
	at := newFakeAT()
	at.on("AT+CMGL=4", cmglListing([2]string{"+CMGL: 6,1,,8", rawPDU}), nil)
	cfg := testConfig()
	cfg.ChatIDs = []int64{100}
	deliverer, sender, _ := newTestDeliverer(cfg)

	if err := processMessages(context.Background(), at, deliverer, cfg, 30); err != nil {
		t.Fatalf("processMessages() error = %v", err)
	}
	sent := sender.sentTo(100)
	if len(sent) != 1 || !strings.Contains(sent[0].Text, rawPDU) {
		t.Fatalf("raw PDU not forwarded: %+v", sent)
	}
	if !strings.Contains(sent[0].Text, "undecodable") {
		t.Error("raw fallback must be clearly marked")
	}
	if at.commandCount("AT+CMGD=6") != 1 {
		t.Error("raw-forwarded slot must be deleted after delivery")
	}
}

// TestProcessMessages_DeleteTransportErrorAborts: an unacknowledged delete on
// a dead session must abort further modem commands.
func TestProcessMessages_DeleteTransportErrorAborts(t *testing.T) {
	t.Cleanup(swapClock(newFakeClock()))
	at := newFakeAT()
	at.on("AT+CMGL=4", cmglListing(
		[2]string{"+CMGL: 1,1,,29", testPDUSingle},
		[2]string{"+CMGL: 2,1,,24", pduAlphaSender},
	), nil)
	at.on("AT+CMGD=1", nil, ErrModemTimeout)
	cfg := testConfig()
	cfg.ChatIDs = []int64{100}
	deliverer, _, _ := newTestDeliverer(cfg)

	err := processMessages(context.Background(), at, deliverer, cfg, 30)
	if err == nil || !IsTimeoutError(err) {
		t.Fatalf("error = %v, want propagated transport error", err)
	}
	if at.commandCount("AT+CMGD=2") != 0 {
		t.Error("no further deletes may run after an unacknowledged delete")
	}
}

// TestBuildTelegramMessages_Chunking: long bodies are split into independently
// valid HTML messages below the visible limit, with escaping intact.
func TestBuildTelegramMessages_Chunking(t *testing.T) {
	body := strings.Repeat("э<&>😀 abc ", 1200) // ~12k runes with HTML-hostile chars
	pending := PendingSMS{
		Message: SMSMessage{
			From: "+123", Text: body,
			Time:        time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC),
			IsMultipart: true, TotalParts: 20,
		},
		PartIndices: []int{1, 2, 3},
	}

	chunks := buildTelegramMessages(pending)
	if len(chunks) < 3 {
		t.Fatalf("chunks = %d, want >= 3 for a 12k-rune body", len(chunks))
	}

	var reassembled strings.Builder
	for i, chunk := range chunks {
		visible := len([]rune(htmlToPlain(chunk)))
		if visible > telegramMaxVisible {
			t.Errorf("chunk %d visible length %d exceeds %d", i, visible, telegramMaxVisible)
		}
		if !strings.Contains(chunk, "<b>From:</b>") {
			t.Errorf("chunk %d lost its header", i)
		}
		if !strings.Contains(chunk, fmt.Sprintf("<b>Chunk:</b> %d/%d", i+1, len(chunks))) {
			t.Errorf("chunk %d missing its chunk marker", i)
		}
		if strings.Contains(htmlToPlain(chunk), "&lt;") {
			t.Errorf("chunk %d double-escaped", i)
		}
		// Extract the escaped body after the blank line and unescape it.
		if idx := strings.LastIndex(chunk, "\n\n"); idx >= 0 {
			reassembled.WriteString(htmlToPlain(chunk[idx+2:]))
		}
	}
	if reassembled.String() != body {
		t.Error("chunked body does not reassemble to the original text")
	}
}

// TestBuildTelegramMessages_ShortSingle: short messages produce exactly one
// message identical to the classic format.
func TestBuildTelegramMessages_ShortSingle(t *testing.T) {
	pending := PendingSMS{
		Message: SMSMessage{From: "+1", Text: "hi <&>", Time: time.Now()},
	}
	chunks := buildTelegramMessages(pending)
	if len(chunks) != 1 {
		t.Fatalf("chunks = %d, want 1", len(chunks))
	}
	if !strings.Contains(chunks[0], "hi &lt;&amp;&gt;") {
		t.Errorf("body not escaped: %q", chunks[0])
	}
	if strings.Contains(chunks[0], "Chunk:") {
		t.Error("single message must not carry a chunk marker")
	}
}

// TestClassifySendError covers the mapping from library errors to policy.
func TestClassifySendError(t *testing.T) {
	tooMany := &bot.TooManyRequestsError{Message: "slow down", RetryAfter: 7}
	tests := []struct {
		name  string
		err   error
		want  sendClass
		after time.Duration
	}{
		{"nil", nil, sendOK, 0},
		{"bad request", fmt.Errorf("%w, too long", bot.ErrorBadRequest), sendContentRejected, 0},
		{"forbidden", fmt.Errorf("%w, kicked", bot.ErrorForbidden), sendDestinationFailed, 0},
		{"unauthorized", fmt.Errorf("%w, bad token", bot.ErrorUnauthorized), sendDestinationFailed, 0},
		{"not found", fmt.Errorf("%w, no chat", bot.ErrorNotFound), sendDestinationFailed, 0},
		{"conflict", fmt.Errorf("%w", bot.ErrorConflict), sendDestinationFailed, 0},
		{"migrate", &bot.MigrateError{Message: "moved", MigrateToChatID: 5}, sendDestinationFailed, 0},
		{"too many requests", tooMany, sendRateLimited, 7 * time.Second},
		{"network", errors.New("dial tcp: timeout"), sendTransient, 0},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			class, after := classifySendError(tt.err)
			if class != tt.want {
				t.Errorf("class = %v, want %v", class, tt.want)
			}
			if tt.after != 0 && after != tt.after {
				t.Errorf("retryAfter = %v, want %v", after, tt.after)
			}
		})
	}
}

// TestDeliverer_PlainFallbackOn400: HTML rejected but plain text accepted —
// the message is delivered (and would be deleted), not stuck.
func TestDeliverer_PlainFallbackOn400(t *testing.T) {
	t.Cleanup(swapClock(newFakeClock()))
	cfg := testConfig()
	cfg.ChatIDs = []int64{100}
	deliverer, sender, _ := newTestDeliverer(cfg)
	sender.script = func(_ int, _ int64, text string) error {
		if strings.Contains(text, "<b>") {
			return fmt.Errorf("%w, can't parse entities", bot.ErrorBadRequest)
		}
		return nil
	}

	status := deliverer.Deliver(context.Background(), PendingSMS{
		Message:     SMSMessage{From: "+1", Text: "body", Time: time.Now()},
		PartIndices: []int{4},
	})
	if status != deliveryDone {
		t.Fatalf("status = %v, want deliveryDone via plain fallback", status)
	}
	if len(sender.sent) != 2 {
		t.Fatalf("sends = %d, want 2 (HTML attempt + plain fallback)", len(sender.sent))
	}
	if strings.Contains(sender.sent[1].Text, "<b>") {
		t.Error("fallback message still contains HTML tags")
	}
}
