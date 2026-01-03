// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"
)

// Specific error types for better error handling
var (
	ErrModemTimeout    = errors.New("modem timeout: no response received")
	ErrModemError      = errors.New("modem returned ERROR")
	ErrModemDisconnect = errors.New("modem disconnected or not responding")
	ErrWriteFailed     = errors.New("failed to write to modem")
)

// SimpleAT is a simple AT command wrapper that actually works with our modem
type SimpleAT struct {
	port    io.ReadWriter
	timeout time.Duration
}

// NewSimpleAT creates a new simple AT command interface
func NewSimpleAT(port io.ReadWriter, timeout time.Duration) *SimpleAT {
	return &SimpleAT{
		port:    port,
		timeout: timeout,
	}
}

// Command sends an AT command and returns the response lines (excluding echo and OK/ERROR)
func (s *SimpleAT) Command(cmd string) ([]string, error) {
	return s.CommandWithTimeout(cmd, s.timeout)
}

// CommandWithTimeout sends an AT command with a custom timeout
func (s *SimpleAT) CommandWithTimeout(cmd string, timeout time.Duration) ([]string, error) {
	// Send command
	fullCmd := cmd + "\r\n"
	_, err := s.port.Write([]byte(fullCmd))
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrWriteFailed, err)
	}

	// Read response with timeout
	deadline := time.Now().Add(timeout)
	reader := bufio.NewReader(s.port)

	var lines []string
	var gotOK, gotERROR bool
	noDataCount := 0
	maxNoData := int(timeout.Milliseconds() / 50) // Max iterations without data
	if maxNoData < 1 {
		maxNoData = 1
	}

	for time.Now().Before(deadline) {
		line, err := reader.ReadString('\n')
		if err != nil && len(line) == 0 {
			noDataCount++

			// If we've been waiting too long without any data, modem might be disconnected
			if noDataCount > maxNoData {
				if len(lines) == 0 {
					return nil, ErrModemDisconnect
				}
				return nil, fmt.Errorf("%w after partial response", ErrModemTimeout)
			}

			// Check if we got OK/ERROR already
			if gotOK || gotERROR {
				break
			}
			// Continue waiting
			time.Sleep(50 * time.Millisecond)
			continue
		}

		// Reset no-data counter on successful read
		noDataCount = 0
		line = strings.TrimSpace(line)

		// Skip empty lines and echo
		if line == "" || line == cmd {
			continue
		}

		// Check for OK/ERROR
		if line == "OK" {
			gotOK = true
			break
		}
		if line == "ERROR" {
			gotERROR = true
			break
		}
		if strings.HasPrefix(line, "+CME ERROR:") {
			// Extended error code from modem
			return nil, fmt.Errorf("%w: %s", ErrModemError, line)
		}
		if strings.HasPrefix(line, "+CMS ERROR:") {
			// SMS-specific error code
			return nil, fmt.Errorf("%w: %s", ErrModemError, line)
		}

		// It's a response line
		lines = append(lines, line)

		// If we got partial data with an error, keep waiting for the rest.
		if err != nil {
			continue
		}
	}

	if gotERROR {
		return nil, ErrModemError
	}

	if !gotOK {
		if len(lines) == 0 {
			return nil, ErrModemTimeout
		}
		return nil, fmt.Errorf("%w: got %d lines but no OK", ErrModemTimeout, len(lines))
	}

	return lines, nil
}

// IsTimeoutError checks if an error is a timeout-related error
func IsTimeoutError(err error) bool {
	return errors.Is(err, ErrModemTimeout) || errors.Is(err, ErrModemDisconnect)
}

// IsModemError checks if an error is a modem ERROR response
func IsModemError(err error) bool {
	return errors.Is(err, ErrModemError)
}

// Ping sends a simple AT command to check if modem is responsive
func (s *SimpleAT) Ping() error {
	_, err := s.CommandWithTimeout("AT", 2*time.Second)
	return err
}
