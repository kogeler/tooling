// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"log/slog"
	"testing"
	"time"
)

// clearConfigEnv resets every configuration variable so host environment
// (e.g. a sourced tokens.sh) cannot leak into tests.
func clearConfigEnv(t *testing.T) {
	t.Helper()
	for _, key := range []string{
		"DRY_RUN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "SERIAL_PORT",
		"BAUD_RATE", "LOG_LEVEL", "MULTIPART_MAX_AGE", "TELEGRAM_SEND_TIMEOUT",
		"NETWORK_REG_GRACE",
	} {
		t.Setenv(key, "")
	}
}

func TestLoadConfigDefaults(t *testing.T) {
	clearConfigEnv(t)
	t.Setenv("TELEGRAM_BOT_TOKEN", "123:abc")
	t.Setenv("TELEGRAM_CHAT_IDS", "42")

	cfg, err := loadConfig()
	if err != nil {
		t.Fatalf("loadConfig() error = %v", err)
	}
	if cfg.SerialPort != "/dev/ttyUSB0" {
		t.Errorf("SerialPort = %q, want /dev/ttyUSB0", cfg.SerialPort)
	}
	if cfg.BaudRate != 115200 {
		t.Errorf("BaudRate = %d, want 115200", cfg.BaudRate)
	}
	if cfg.LogLevel != slog.LevelInfo {
		t.Errorf("LogLevel = %v, want info", cfg.LogLevel)
	}
	if cfg.TelegramSendTimeout != 20*time.Second {
		t.Errorf("TelegramSendTimeout = %v, want 20s", cfg.TelegramSendTimeout)
	}
	if cfg.NetworkRegGrace != 90*time.Second {
		t.Errorf("NetworkRegGrace = %v, want 90s", cfg.NetworkRegGrace)
	}
	if cfg.MultipartMaxAge != 0 {
		t.Errorf("MultipartMaxAge = %v, want 0", cfg.MultipartMaxAge)
	}
	if cfg.DryRun {
		t.Error("DryRun = true, want false")
	}
}

func TestLoadConfigChatIDs(t *testing.T) {
	tests := []struct {
		name    string
		ids     string
		want    []int64
		wantErr bool
	}{
		{"single", "42", []int64{42}, false},
		{"multiple with spaces", " -100123, 42 ,7", []int64{-100123, 42, 7}, false},
		{"trailing comma", "42,", []int64{42}, false},
		{"duplicates removed order kept", "42,-1,42,-1,7", []int64{42, -1, 7}, false},
		{"zero rejected", "42,0", nil, true},
		{"garbage rejected", "42,abc", nil, true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			clearConfigEnv(t)
			t.Setenv("TELEGRAM_BOT_TOKEN", "123:abc")
			t.Setenv("TELEGRAM_CHAT_IDS", tt.ids)

			cfg, err := loadConfig()
			if (err != nil) != tt.wantErr {
				t.Fatalf("loadConfig() error = %v, wantErr %v", err, tt.wantErr)
			}
			if err != nil {
				return
			}
			if len(cfg.ChatIDs) != len(tt.want) {
				t.Fatalf("ChatIDs = %v, want %v", cfg.ChatIDs, tt.want)
			}
			for i := range tt.want {
				if cfg.ChatIDs[i] != tt.want[i] {
					t.Errorf("ChatIDs = %v, want %v", cfg.ChatIDs, tt.want)
					break
				}
			}
		})
	}
}

func TestLoadConfigRequiredVars(t *testing.T) {
	clearConfigEnv(t)
	if _, err := loadConfig(); err == nil {
		t.Error("missing token should be an error")
	}

	clearConfigEnv(t)
	t.Setenv("TELEGRAM_BOT_TOKEN", "123:abc")
	if _, err := loadConfig(); err == nil {
		t.Error("missing chat IDs should be an error")
	}
}

func TestLoadConfigDryRunRelaxesRequirements(t *testing.T) {
	for _, v := range []string{"true", "TRUE", "1", "yes"} {
		t.Run(v, func(t *testing.T) {
			clearConfigEnv(t)
			t.Setenv("DRY_RUN", v)
			cfg, err := loadConfig()
			if err != nil {
				t.Fatalf("loadConfig() with DRY_RUN=%s error = %v", v, err)
			}
			if !cfg.DryRun {
				t.Errorf("DryRun = false with DRY_RUN=%s", v)
			}
		})
	}

	clearConfigEnv(t)
	t.Setenv("DRY_RUN", "false")
	if _, err := loadConfig(); err == nil {
		t.Error("DRY_RUN=false without token should be an error")
	}
}

func TestLoadConfigNumericValidation(t *testing.T) {
	tests := []struct {
		name  string
		key   string
		value string
	}{
		{"baud zero", "BAUD_RATE", "0"},
		{"baud negative", "BAUD_RATE", "-9600"},
		{"baud garbage", "BAUD_RATE", "fast"},
		{"send timeout zero", "TELEGRAM_SEND_TIMEOUT", "0s"},
		{"send timeout negative", "TELEGRAM_SEND_TIMEOUT", "-5s"},
		{"send timeout garbage", "TELEGRAM_SEND_TIMEOUT", "twenty"},
		{"grace negative", "NETWORK_REG_GRACE", "-1m"},
		{"max age negative", "MULTIPART_MAX_AGE", "-72h"},
		{"log level garbage", "LOG_LEVEL", "verbose"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			clearConfigEnv(t)
			t.Setenv("TELEGRAM_BOT_TOKEN", "123:abc")
			t.Setenv("TELEGRAM_CHAT_IDS", "42")
			t.Setenv(tt.key, tt.value)
			if _, err := loadConfig(); err == nil {
				t.Errorf("loadConfig() with %s=%q should fail", tt.key, tt.value)
			}
		})
	}
}

func TestLoadConfigDurationsAndLevels(t *testing.T) {
	clearConfigEnv(t)
	t.Setenv("TELEGRAM_BOT_TOKEN", "123:abc")
	t.Setenv("TELEGRAM_CHAT_IDS", "42")
	t.Setenv("TELEGRAM_SEND_TIMEOUT", "7s")
	t.Setenv("NETWORK_REG_GRACE", "0")
	t.Setenv("MULTIPART_MAX_AGE", "72h")
	t.Setenv("LOG_LEVEL", "warning")
	t.Setenv("BAUD_RATE", "9600")

	cfg, err := loadConfig()
	if err != nil {
		t.Fatalf("loadConfig() error = %v", err)
	}
	if cfg.TelegramSendTimeout != 7*time.Second {
		t.Errorf("TelegramSendTimeout = %v, want 7s", cfg.TelegramSendTimeout)
	}
	if cfg.NetworkRegGrace != 0 {
		t.Errorf("NetworkRegGrace = %v, want 0", cfg.NetworkRegGrace)
	}
	if cfg.MultipartMaxAge != 72*time.Hour {
		t.Errorf("MultipartMaxAge = %v, want 72h", cfg.MultipartMaxAge)
	}
	if cfg.LogLevel != slog.LevelWarn {
		t.Errorf("LogLevel = %v, want warn", cfg.LogLevel)
	}
	if cfg.BaudRate != 9600 {
		t.Errorf("BaudRate = %d, want 9600", cfg.BaudRate)
	}
}
