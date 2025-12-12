// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"strings"
	"testing"
	"time"
)

func TestFormatTelegramMessage(t *testing.T) {
	tests := []struct {
		name     string
		msg      SMSMessage
		wantFrom string
		wantSMSC string
		wantTime string
	}{
		{
			name: "simple message with SMSC",
			msg: SMSMessage{
				Index: 1,
				From:  "+1234567890",
				Text:  "Test message",
				Time:  time.Date(2025, 12, 11, 18, 21, 49, 0, time.UTC),
				SMSC:  "+1987654321",
			},
			wantFrom: "+1234567890",
			wantSMSC: "+1987654321",
			wantTime: "2025-12-11 18:21:49",
		},
		{
			name: "multipart message shows parts count",
			msg: SMSMessage{
				Index:       3,
				From:        "+1234567890",
				Text:        "Long message text",
				Time:        time.Date(2025, 12, 11, 18, 21, 49, 0, time.UTC),
				SMSC:        "+1987654321",
				IsMultipart: true,
				TotalParts:  3,
			},
			wantFrom: "+1234567890",
			wantSMSC: "+1987654321",
		},
		{
			name: "message without SMSC",
			msg: SMSMessage{
				Index: 1,
				From:  "+1234567890",
				Text:  "Test",
				Time:  time.Now(),
				SMSC:  "",
			},
			wantFrom: "+1234567890",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := formatTelegramMessage(tt.msg)

			if tt.wantFrom != "" && !strings.Contains(result, tt.wantFrom) {
				t.Errorf("formatTelegramMessage() missing From: %s", tt.wantFrom)
			}
			if tt.wantSMSC != "" && !strings.Contains(result, tt.wantSMSC) {
				t.Errorf("formatTelegramMessage() missing SMSC: %s", tt.wantSMSC)
			}
			if tt.wantTime != "" && !strings.Contains(result, tt.wantTime) {
				t.Errorf("formatTelegramMessage() missing Time: %s", tt.wantTime)
			}
			if tt.msg.IsMultipart && !strings.Contains(result, "Parts:") {
				t.Error("formatTelegramMessage() missing Parts count for multipart")
			}
			if tt.msg.SMSC == "" && strings.Contains(result, "SMSC:") {
				t.Error("formatTelegramMessage() should not show SMSC when empty")
			}
		})
	}
}

func TestEscapeHTML(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"hello", "hello"},
		{"<script>", "&lt;script&gt;"},
		{"a & b", "a &amp; b"},
		{"1 < 2 > 0", "1 &lt; 2 &gt; 0"},
		{"", ""},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := escapeHTML(tt.input)
			if got != tt.want {
				t.Errorf("escapeHTML(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}
