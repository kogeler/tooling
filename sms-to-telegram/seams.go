// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"time"

	"github.com/go-telegram/bot"
	"github.com/go-telegram/bot/models"
)

// TelegramSender is the narrow surface of the Telegram bot the pipeline uses.
// *bot.Bot satisfies it; tests substitute a fake.
type TelegramSender interface {
	SendMessage(ctx context.Context, params *bot.SendMessageParams) (*models.Message, error)
}

// ATCommander is the narrow surface of the AT modem session used by the
// diagnostics and SMS pipeline. *SimpleAT satisfies it; tests substitute a fake.
type ATCommander interface {
	Command(cmd string) ([]string, error)
	CommandWithTimeout(cmd string, timeout time.Duration) ([]string, error)
	Ping() error
}

// Clock abstracts time for deterministic tests of retry/backoff/grace logic.
// Production code uses systemClock; tests swap the package-level clk.
type Clock interface {
	Now() time.Time
	After(d time.Duration) <-chan time.Time
	Sleep(d time.Duration)
}

type systemClock struct{}

func (systemClock) Now() time.Time                         { return time.Now() }
func (systemClock) After(d time.Duration) <-chan time.Time { return time.After(d) }
func (systemClock) Sleep(d time.Duration)                  { time.Sleep(d) }

// clk is the process-wide clock. Tests that replace it must restore it and
// must not run in parallel with each other.
var clk Clock = systemClock{}
