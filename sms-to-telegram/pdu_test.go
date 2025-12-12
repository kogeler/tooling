package main

import (
	"testing"
	"time"
)

func TestDecodePhoneNumber(t *testing.T) {
	tests := []struct {
		name          string
		data          []byte
		international bool
		want          string
	}{
		{
			name:          "international number",
			data:          []byte{0x21, 0x43, 0x65, 0x87, 0x09},
			international: true,
			want:          "+1234567890",
		},
		{
			name:          "local number",
			data:          []byte{0x21, 0x43, 0x65},
			international: false,
			want:          "123456",
		},
		{
			name:          "number with F padding",
			data:          []byte{0x21, 0x43, 0xF5}, // 12345 (F is filler)
			international: false,
			want:          "12345",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := decodePhoneNumber(tt.data, tt.international)
			if got != tt.want {
				t.Errorf("decodePhoneNumber() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestDecodeSCTS(t *testing.T) {
	// 24/12/11 15:30:45 UTC+3 (12 quarter hours)
	// BCD swapped: 42 21 11 51 03 54 21
	data := []byte{0x42, 0x21, 0x11, 0x51, 0x03, 0x54, 0x21}

	got := decodeSCTS(data)

	if got.Year() != 2024 {
		t.Errorf("year = %d, want 2024", got.Year())
	}
	if got.Month() != 12 {
		t.Errorf("month = %d, want 12", got.Month())
	}
	if got.Day() != 11 {
		t.Errorf("day = %d, want 11", got.Day())
	}
	if got.Hour() != 15 {
		t.Errorf("hour = %d, want 15", got.Hour())
	}
	if got.Minute() != 30 {
		t.Errorf("minute = %d, want 30", got.Minute())
	}
	if got.Second() != 45 {
		t.Errorf("second = %d, want 45", got.Second())
	}

	// Check timezone offset (UTC+3 = 3*60*60 = 10800 seconds)
	_, offset := got.Zone()
	if offset != 10800 {
		t.Errorf("timezone offset = %d, want 10800", offset)
	}
}

func TestDecodeGSM7Bit(t *testing.T) {
	tests := []struct {
		name     string
		data     []byte
		numChars int
		fillBits int
		want     string
	}{
		{
			name:     "simple hello",
			data:     []byte{0xC8, 0x32, 0x9B, 0xFD, 0x06}, // "Hello"
			numChars: 5,
			fillBits: 0,
			want:     "Hello",
		},
		{
			name:     "test message",
			data:     []byte{0xD4, 0xF2, 0x9C, 0x0E}, // "Test"
			numChars: 4,
			fillBits: 0,
			want:     "Test",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := decodeGSM7Bit(tt.data, tt.numChars, tt.fillBits)
			if got != tt.want {
				t.Errorf("decodeGSM7Bit() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestDecodeUCS2(t *testing.T) {
	tests := []struct {
		name string
		data []byte
		want string
	}{
		{
			name: "cyrillic text Привет",
			data: []byte{
				0x04, 0x1F, // П
				0x04, 0x40, // р
				0x04, 0x38, // и
				0x04, 0x32, // в
				0x04, 0x35, // е
				0x04, 0x42, // т
			},
			want: "Привет",
		},
		{
			name: "english hello",
			data: []byte{
				0x00, 0x48, // H
				0x00, 0x65, // e
				0x00, 0x6C, // l
				0x00, 0x6C, // l
				0x00, 0x6F, // o
			},
			want: "Hello",
		},
		{
			name: "mixed cyrillic and numbers",
			data: []byte{
				0x04, 0x22, // Т
				0x04, 0x35, // е
				0x04, 0x41, // с
				0x04, 0x42, // т
				0x00, 0x20, // space
				0x00, 0x31, // 1
				0x00, 0x32, // 2
				0x00, 0x33, // 3
			},
			want: "Тест 123",
		},
		{
			name: "emoji (surrogate pair)",
			data: []byte{
				0xD8, 0x3D, 0xDE, 0x00, // 😀 U+1F600
			},
			want: "😀",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := decodeUCS2(tt.data)
			if got != tt.want {
				t.Errorf("decodeUCS2() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestParsePDU(t *testing.T) {
	tests := []struct {
		name        string
		pduHex      string
		wantSender  string
		wantSMSC    string
		wantText    string
		wantErr     bool
		isMultipart bool
	}{
		{
			name:    "invalid hex",
			pduHex:  "ZZZZ",
			wantErr: true,
		},
		{
			name:    "too short",
			pduHex:  "00",
			wantErr: true,
		},
		{
			name:        "multipart UCS2 SMS",
			pduHex:      "0791534874894380400C915348948470870008522111811294808C0500030B0301041D043004470430043B043E000A0440044004400440044004400440044004400440043F043F043F043F043F043F043F043F043F043F043F0440043F04300440043F043F043F0440043F043504350440043D0440043D043D0440044004400440044004400440044004400440044004400440044004400440043E043E043E043F043F04300430",
			isMultipart: true,
			// Note: PDU contains actual encoded numbers, but we only verify structure
		},
		{
			name:     "simple UCS2 SMS",
			pduHex:   "0791534874894370000C915348948470870008522111218305800A04220435044104420031",
			wantText: "Тест1",
			// Note: PDU contains actual encoded numbers, but we only verify text decoding
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			msg, err := ParsePDU(tt.pduHex)
			if (err != nil) != tt.wantErr {
				t.Errorf("ParsePDU() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if err != nil {
				return
			}
			if tt.wantSender != "" && msg.Sender != tt.wantSender {
				t.Errorf("ParsePDU() sender = %q, want %q", msg.Sender, tt.wantSender)
			}
			if tt.wantSMSC != "" && msg.SMSC != tt.wantSMSC {
				t.Errorf("ParsePDU() SMSC = %q, want %q", msg.SMSC, tt.wantSMSC)
			}
			if tt.wantText != "" && msg.Text != tt.wantText {
				t.Errorf("ParsePDU() text = %q, want %q", msg.Text, tt.wantText)
			}
			if tt.isMultipart && !msg.IsMultipart {
				t.Errorf("ParsePDU() IsMultipart = false, want true")
			}
		})
	}
}

func TestMultipartCollector(t *testing.T) {
	collector := NewMultipartCollector()

	testTime := time.Date(2025, 12, 11, 18, 21, 49, 0, time.UTC)

	// Simulate 3-part message with SMSC
	part1 := &PDUMessage{
		Sender:       "+1234567890",
		Timestamp:    testTime,
		Text:         "Part 1 text. ",
		SMSC:         "+1987654321",
		IsMultipart:  true,
		MultipartRef: 42,
		PartNumber:   1,
		TotalParts:   3,
	}

	part2 := &PDUMessage{
		Sender:       "+1234567890",
		Timestamp:    testTime,
		Text:         "Part 2 text. ",
		SMSC:         "+1987654321",
		IsMultipart:  true,
		MultipartRef: 42,
		PartNumber:   2,
		TotalParts:   3,
	}

	part3 := &PDUMessage{
		Sender:       "+1234567890",
		Timestamp:    testTime,
		Text:         "Part 3 text.",
		SMSC:         "+1987654321",
		IsMultipart:  true,
		MultipartRef: 42,
		PartNumber:   3,
		TotalParts:   3,
	}

	// Add parts out of order
	result := collector.Add(part2)
	if result != nil {
		t.Error("Should return nil when message incomplete")
	}
	if collector.Pending() != 1 {
		t.Errorf("Pending() = %d, want 1", collector.Pending())
	}

	result = collector.Add(part1)
	if result != nil {
		t.Error("Should return nil when message incomplete")
	}
	if collector.Pending() != 1 {
		t.Errorf("Pending() = %d, want 1", collector.Pending())
	}

	result = collector.Add(part3)
	if result == nil {
		t.Fatal("Should return complete message")
	}

	expectedText := "Part 1 text. Part 2 text. Part 3 text."
	if result.Text != expectedText {
		t.Errorf("Assembled text = %q, want %q", result.Text, expectedText)
	}

	// Verify SMSC is preserved from first part
	if result.SMSC != "+1987654321" {
		t.Errorf("Assembled SMSC = %q, want %q", result.SMSC, "+1987654321")
	}

	// Verify Sender is preserved
	if result.Sender != "+1234567890" {
		t.Errorf("Assembled Sender = %q, want %q", result.Sender, "+1234567890")
	}

	// Verify Timestamp is preserved from first part
	if !result.Timestamp.Equal(testTime) {
		t.Errorf("Assembled Timestamp = %v, want %v", result.Timestamp, testTime)
	}

	if collector.Pending() != 0 {
		t.Errorf("Pending() = %d, want 0 after assembly", collector.Pending())
	}
}

func TestMultipartCollectorDifferentSenders(t *testing.T) {
	collector := NewMultipartCollector()

	// Two different senders with same ref number
	msg1 := &PDUMessage{
		Sender:       "+1111111111",
		Text:         "From sender 1 part 1",
		IsMultipart:  true,
		MultipartRef: 1,
		PartNumber:   1,
		TotalParts:   2,
	}

	msg2 := &PDUMessage{
		Sender:       "+2222222222",
		Text:         "From sender 2 part 1",
		IsMultipart:  true,
		MultipartRef: 1,
		PartNumber:   1,
		TotalParts:   2,
	}

	collector.Add(msg1)
	collector.Add(msg2)

	// Should have 2 pending (different senders)
	if collector.Pending() != 2 {
		t.Errorf("Pending() = %d, want 2", collector.Pending())
	}
}

func TestMultipartCollectorSinglePart(t *testing.T) {
	collector := NewMultipartCollector()

	// Non-multipart message should be returned immediately
	msg := &PDUMessage{
		Sender:      "+1234567890",
		Text:        "Single message",
		IsMultipart: false,
	}

	result := collector.Add(msg)
	if result == nil {
		t.Fatal("Non-multipart should be returned immediately")
	}
	if result.Text != "Single message" {
		t.Errorf("Text = %q, want %q", result.Text, "Single message")
	}
}

func TestParseUDH(t *testing.T) {
	msg := &PDUMessage{}

	// 8-bit reference UDH: IEI=00, len=03, ref=42, total=3, part=2
	udh := []byte{0x00, 0x03, 0x2A, 0x03, 0x02}
	msg.parseUDH(udh)

	if !msg.IsMultipart {
		t.Error("IsMultipart should be true")
	}
	if msg.MultipartRef != 42 {
		t.Errorf("MultipartRef = %d, want 42", msg.MultipartRef)
	}
	if msg.TotalParts != 3 {
		t.Errorf("TotalParts = %d, want 3", msg.TotalParts)
	}
	if msg.PartNumber != 2 {
		t.Errorf("PartNumber = %d, want 2", msg.PartNumber)
	}
}

func TestParseUDH16BitRef(t *testing.T) {
	msg := &PDUMessage{}

	// 16-bit reference UDH: IEI=08, len=04, ref=0x0102, total=5, part=3
	udh := []byte{0x08, 0x04, 0x01, 0x02, 0x05, 0x03}
	msg.parseUDH(udh)

	if !msg.IsMultipart {
		t.Error("IsMultipart should be true")
	}
	if msg.MultipartRef != 258 { // 0x0102
		t.Errorf("MultipartRef = %d, want 258", msg.MultipartRef)
	}
	if msg.TotalParts != 5 {
		t.Errorf("TotalParts = %d, want 5", msg.TotalParts)
	}
	if msg.PartNumber != 3 {
		t.Errorf("PartNumber = %d, want 3", msg.PartNumber)
	}
}

func TestGSM7BitSpecialChars(t *testing.T) {
	// Test GSM alphabet special characters
	tests := []struct {
		septet byte
		want   rune
	}{
		{0x00, '@'},
		{0x01, '£'},
		{0x02, '$'},
		{0x0A, '\n'},
		{0x0D, '\r'},
		{0x20, ' '},
		{0x30, '0'},
		{0x41, 'A'},
		{0x61, 'a'},
	}

	for _, tt := range tests {
		if gsm7BitDefault[tt.septet] != tt.want {
			t.Errorf("gsm7BitDefault[%02X] = %q, want %q", tt.septet, gsm7BitDefault[tt.septet], tt.want)
		}
	}
}

func TestGSM7BitExtension(t *testing.T) {
	// Test extension characters accessed via escape (0x1B)
	tests := []struct {
		code byte
		want rune
	}{
		{0x14, '^'},
		{0x28, '{'},
		{0x29, '}'},
		{0x2F, '\\'},
		{0x3C, '['},
		{0x3D, '~'},
		{0x3E, ']'},
		{0x40, '|'},
		{0x65, '€'},
	}

	for _, tt := range tests {
		got, ok := gsm7BitExtension[tt.code]
		if !ok {
			t.Errorf("gsm7BitExtension[%02X] not found", tt.code)
			continue
		}
		if got != tt.want {
			t.Errorf("gsm7BitExtension[%02X] = %q, want %q", tt.code, got, tt.want)
		}
	}
}
