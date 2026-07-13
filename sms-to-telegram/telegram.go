// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/go-telegram/bot"
	"github.com/go-telegram/bot/models"
)

// Telegram's sendMessage accepts 1-4096 characters counted after entity
// parsing. Chunks are budgeted on unescaped runes with a safety margin, so an
// HTML-escaped chunk can never exceed the visible limit.
const (
	telegramMaxVisible = 4096
	chunkSafetyMargin  = 64
)

// sendClass classifies a SendMessage error for retry policy.
type sendClass int

const (
	sendOK sendClass = iota
	// sendTransient: network/5xx — retryable; the SIM-backed poll cycle is
	// the outer retry loop.
	sendTransient
	// sendRateLimited: 429 with a retry_after — back off that chat.
	sendRateLimited
	// sendContentRejected: 400 — the payload itself was refused; a plain-text
	// fallback may be attempted, retrying the same payload is pointless.
	sendContentRejected
	// sendDestinationFailed: 401/403/404/409/migrate — token or chat
	// configuration problem; affects every message, retrying is pointless
	// until the operator fixes it.
	sendDestinationFailed
)

// classifySendError maps go-telegram/bot errors onto retry policy classes.
func classifySendError(err error) (sendClass, time.Duration) {
	if err == nil {
		return sendOK, 0
	}
	var tooMany *bot.TooManyRequestsError
	if errors.As(err, &tooMany) {
		retryAfter := time.Duration(tooMany.RetryAfter) * time.Second
		if retryAfter <= 0 {
			retryAfter = 30 * time.Second
		}
		return sendRateLimited, retryAfter
	}
	var migrate *bot.MigrateError
	if errors.As(err, &migrate) {
		return sendDestinationFailed, 0
	}
	if errors.Is(err, bot.ErrorBadRequest) {
		return sendContentRejected, 0
	}
	if errors.Is(err, bot.ErrorForbidden) || errors.Is(err, bot.ErrorUnauthorized) ||
		errors.Is(err, bot.ErrorNotFound) || errors.Is(err, bot.ErrorConflict) {
		return sendDestinationFailed, 0
	}
	return sendTransient, 0
}

// deliveryStatus is the outcome of delivering one PendingSMS.
type deliveryStatus int

const (
	// deliveryDone: every chunk reached every configured chat — the SIM
	// slots may be deleted.
	deliveryDone deliveryStatus = iota
	// deliveryRejected: Telegram permanently rejected the content; the SMS
	// stays on the SIM, an operator alert was emitted once, and the message
	// is skipped (not re-sent) until process restart. Later SMS proceed.
	deliveryRejected
	// deliveryDeferred: transient failure, rate limit or destination
	// misconfiguration — retain everything and let the next poll retry.
	deliveryDeferred
)

// Deliverer sends assembled SMS to all chats with per-chat 429 cooldowns,
// bounded transient retries, a plain-text fallback for content-rejected HTML,
// and a once-per-message operator alert for permanent rejections.
// It persists across modem session reopens (created once in run()).
type Deliverer struct {
	sender   TelegramSender
	notifier *ErrorNotifier
	cfg      *Config

	cooldownUntil map[int64]time.Time
	// rejected remembers permanently rejected messages (by content
	// fingerprint) so they are not re-sent to already-delivered chats on
	// every poll; the SIM slot stays until removed manually.
	rejected map[string]struct{}
	// destIssue tracks per-chat destination failures (kicked bot, deleted
	// chat) with stateless dedup, deliberately OUTSIDE the modem-recovery
	// state machine: a modem session restart must not announce a false
	// "Recovered" while a Telegram destination is still broken.
	destIssue map[int64]bool
}

func NewDeliverer(sender TelegramSender, notifier *ErrorNotifier, cfg *Config) *Deliverer {
	return &Deliverer{
		sender:        sender,
		notifier:      notifier,
		cfg:           cfg,
		cooldownUntil: make(map[int64]time.Time),
		rejected:      make(map[string]struct{}),
		destIssue:     make(map[int64]bool),
	}
}

// transientRetryDelays: short in-place retries for transient errors. The SIM
// is the durable queue, so long in-loop backoff would only stall polling —
// the next poll cycle is the real retry.
var transientRetryDelays = []time.Duration{5 * time.Second, 10 * time.Second}

// Deliver forwards one pending SMS to every configured chat.
func (d *Deliverer) Deliver(ctx context.Context, pending PendingSMS) deliveryStatus {
	chunks := buildTelegramMessages(pending)
	// The SIM indices are part of the identity: two identical SMS in
	// different slots are distinct deliveries.
	key := contentFingerprint(fmt.Sprint(pending.PartIndices) + "\x00" + strings.Join(chunks, "\x00"))

	if _, isRejected := d.rejected[key]; isRejected {
		slog.Debug("Skipping previously rejected message", "index", pending.Message.Index)
		return deliveryRejected
	}

	if d.cfg.DryRun {
		for i, chunk := range chunks {
			slog.Info("DRY_RUN: Would send to Telegram",
				"chat_ids", d.cfg.ChatIDs,
				"chunk", fmt.Sprintf("%d/%d", i+1, len(chunks)),
				"text_length", len(chunk),
				"text_fingerprint", contentFingerprint(chunk),
			)
			slog.Debug("DRY_RUN message content", "text", chunk)
		}
		return deliveryDone
	}

	if d.sender == nil {
		slog.Error("Telegram sender not initialized")
		return deliveryDeferred
	}

	// All chats must be available before the first chunk goes out: partially
	// delivering and retrying later multiplies duplicates.
	now := clk.Now()
	for _, chatID := range d.cfg.ChatIDs {
		if until, ok := d.cooldownUntil[chatID]; ok && now.Before(until) {
			slog.Info("Chat in rate-limit cooldown, deferring delivery",
				"chat_id", chatID, "until", until)
			return deliveryDeferred
		}
	}

	for _, chatID := range d.cfg.ChatIDs {
		for i, chunk := range chunks {
			status := d.sendChunk(ctx, chatID, chunk)
			if status == deliveryRejected {
				d.rejected[key] = struct{}{}
				d.alertRejected(ctx, pending)
				return deliveryRejected
			}
			if status != deliveryDone {
				return status
			}
			slog.Debug("Chunk delivered", "chat_id", chatID, "chunk", i+1, "total", len(chunks))
		}
	}

	return deliveryDone
}

// sendChunk sends one message to one chat, applying the retry policy.
func (d *Deliverer) sendChunk(ctx context.Context, chatID int64, text string) deliveryStatus {
	plainFallbackTried := false
	parseMode := models.ParseModeHTML
	payload := text

	for attempt := 0; ; attempt++ {
		if ctx.Err() != nil {
			return deliveryDeferred
		}

		sendCtx, cancel := context.WithTimeout(ctx, d.cfg.TelegramSendTimeout)
		_, err := d.sender.SendMessage(sendCtx, &bot.SendMessageParams{
			ChatID:    chatID,
			Text:      payload,
			ParseMode: parseMode,
		})
		cancel()

		class, retryAfter := classifySendError(err)
		switch class {
		case sendOK:
			d.clearDestinationFailure(ctx, chatID)
			return deliveryDone

		case sendRateLimited:
			d.cooldownUntil[chatID] = clk.Now().Add(retryAfter)
			slog.Warn("Telegram rate limit, cooling chat down",
				"chat_id", chatID, "retry_after", retryAfter)
			return deliveryDeferred

		case sendDestinationFailed:
			slog.Error("Telegram destination/configuration error",
				"chat_id", chatID, "error", err)
			d.alertDestinationFailure(ctx, chatID, err)
			return deliveryDeferred

		case sendContentRejected:
			if !plainFallbackTried {
				// The HTML payload was refused (parse entities, length after
				// parsing, …). One plain-text attempt with the tags stripped.
				plainFallbackTried = true
				parseMode = ""
				payload = htmlToPlain(text)
				slog.Warn("Telegram rejected HTML content, retrying as plain text",
					"chat_id", chatID, "error", err)
				continue
			}
			slog.Error("Telegram permanently rejected message content",
				"chat_id", chatID, "error", err)
			return deliveryRejected

		case sendTransient:
			if attempt >= len(transientRetryDelays) {
				slog.Warn("Transient Telegram failure, deferring to next poll",
					"chat_id", chatID, "attempts", attempt+1, "error", err)
				return deliveryDeferred
			}
			delay := transientRetryDelays[attempt]
			slog.Warn("Transient Telegram failure, retrying",
				"chat_id", chatID, "attempt", attempt+1, "retry_in", delay, "error", err)
			select {
			case <-ctx.Done():
				return deliveryDeferred
			case <-clk.After(delay):
			}
		}
	}
}

// alertDestinationFailure broadcasts a once-per-chat alert about a broken
// destination (kicked bot, deleted chat, bad token). Stateless with its own
// dedup — see the destIssue field comment.
func (d *Deliverer) alertDestinationFailure(ctx context.Context, chatID int64, sendErr error) {
	if d.destIssue[chatID] {
		return
	}
	d.destIssue[chatID] = true

	msg := fmt.Sprintf("<b>SMS Gateway Alert</b>\n\n"+
		"<b>Error:</b> Telegram rejects deliveries to chat <code>%d</code>\n"+
		"<b>Details:</b> %s\n\n"+
		"<i>Check that the bot is still a member of that chat and the token is valid. SMS are retained on the SIM until delivery succeeds.</i>",
		chatID, escapeHTML(sendErr.Error()))
	if err := d.notifier.sendToTelegram(ctx, msg); err != nil {
		slog.Error("Failed to send destination-failure alert", "error", err)
		d.destIssue[chatID] = false // re-arm so the alert is retried
	}
}

// clearDestinationFailure sends a one-time notice when a previously failing
// destination accepts messages again.
func (d *Deliverer) clearDestinationFailure(ctx context.Context, chatID int64) {
	if !d.destIssue[chatID] {
		return
	}
	d.destIssue[chatID] = false

	msg := fmt.Sprintf("<b>SMS Gateway Recovered</b>\n\n"+
		"<b>Status:</b> Deliveries to chat <code>%d</code> work again", chatID)
	if err := d.notifier.sendToTelegram(ctx, msg); err != nil {
		slog.Error("Failed to send destination-recovery notice", "error", err)
	}
}

// alertRejected notifies the operator (once per message — the caller dedups
// via the rejected set) that an SMS is stuck on the SIM.
func (d *Deliverer) alertRejected(ctx context.Context, pending PendingSMS) {
	msg := fmt.Sprintf("<b>SMS Gateway Alert</b>\n\n"+
		"<b>Error:</b> Telegram permanently rejected a forwarded SMS\n"+
		"<b>From:</b> <code>%s</code>\n"+
		"<b>SIM slot(s):</b> %s\n\n"+
		"<i>The SMS is kept on the SIM and will occupy its slot until removed manually (e.g. AT+CMGD).</i>",
		escapeHTML(pending.Message.From),
		escapeHTML(fmt.Sprint(pending.PartIndices)))

	if err := d.notifier.sendToTelegram(ctx, msg); err != nil {
		slog.Error("Failed to send rejected-message alert", "error", err)
	}
}

// htmlToPlain strips the fixed formatting tags and unescapes entities for the
// plain-text fallback.
func htmlToPlain(s string) string {
	replacer := strings.NewReplacer(
		"<b>", "", "</b>", "",
		"<i>", "", "</i>", "",
		"<code>", "", "</code>", "",
		"&lt;", "<", "&gt;", ">", "&amp;", "&",
	)
	return replacer.Replace(s)
}

// buildTelegramMessages renders a pending SMS into one or more ready-to-send
// HTML messages, each safely below Telegram's visible-length limit.
func buildTelegramMessages(pending PendingSMS) []string {
	if pending.RawFallback {
		return []string{formatRawFallbackMessage(pending.Message, pending.RawReason)}
	}

	msg := pending.Message
	header := formatMessageHeader(msg)
	headerVisible := len([]rune(htmlToPlain(header)))
	budget := telegramMaxVisible - chunkSafetyMargin - headerVisible
	if budget < 256 {
		budget = 256
	}

	body := []rune(msg.Text)
	if len(body) <= budget {
		return []string{header + "\n" + escapeHTML(msg.Text)}
	}

	total := (len(body) + budget - 1) / budget
	messages := make([]string, 0, total)
	for i := 0; i < total; i++ {
		start := i * budget
		end := start + budget
		if end > len(body) {
			end = len(body)
		}
		part := fmt.Sprintf("%s\n<b>Chunk:</b> %d/%d\n\n%s",
			header, i+1, total, escapeHTML(string(body[start:end])))
		messages = append(messages, part)
	}
	return messages
}

// formatMessageHeader renders the metadata block shared by all chunks.
func formatMessageHeader(msg SMSMessage) string {
	var sb strings.Builder
	sb.WriteString("<b>SMS Received</b>\n\n")
	sb.WriteString(fmt.Sprintf("<b>From:</b> <code>%s</code>\n", escapeHTML(msg.From)))
	sb.WriteString(fmt.Sprintf("<b>Time:</b> %s\n", formatMessageTime(msg.Time)))
	if msg.SMSC != "" {
		sb.WriteString(fmt.Sprintf("<b>SMSC:</b> %s\n", escapeHTML(msg.SMSC)))
	}
	if msg.IsMultipart {
		sb.WriteString(fmt.Sprintf("<b>Parts:</b> %d\n", msg.TotalParts))
	}
	return sb.String()
}

// formatMessageTime renders the SMS timestamp; a zero time means the PDU
// carried an invalid SCTS.
func formatMessageTime(t time.Time) string {
	if t.IsZero() {
		return "unknown (invalid timestamp)"
	}
	return t.Format("2006-01-02 15:04:05")
}

// formatRawFallbackMessage renders an undecodable-but-framed PDU so its
// content is preserved for the operator before the slot is freed.
func formatRawFallbackMessage(msg SMSMessage, reason string) string {
	var sb strings.Builder
	sb.WriteString("<b>SMS Received (undecodable)</b>\n\n")
	if msg.From != "" {
		sb.WriteString(fmt.Sprintf("<b>From:</b> <code>%s</code>\n", escapeHTML(msg.From)))
	}
	if !msg.Time.IsZero() {
		sb.WriteString(fmt.Sprintf("<b>Time:</b> %s\n", formatMessageTime(msg.Time)))
	}
	sb.WriteString(fmt.Sprintf("<b>Problem:</b> %s\n", escapeHTML(reason)))
	sb.WriteString(fmt.Sprintf("\n<b>Raw PDU:</b>\n<code>%s</code>", escapeHTML(msg.Text)))
	return sb.String()
}
