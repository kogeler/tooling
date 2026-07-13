// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"errors"
	"testing"
	"time"
)

// Hand-built PDUs (SMSC/OA reuse the captured vectors from pdu_test.go).
const (
	// Alphanumeric sender "Google" (TOA 0xD0), GSM7 body "Hello".
	pduAlphaSender = "07915348748943700" + "40BD0C7F7FBCC2E03" + "0000" + "52211121830580" + "05" + "C8329BFD06"
	// GSM7 multipart (8-bit ref 42, 2 parts) with UDH → 1 fill bit.
	pduGSM7Part1 = "0791534874894370" + "44" + "0C91534894847087" + "0000" + "52211121830580" + "0C" + "0500032A0201" + "906536FB0D"
	pduGSM7Part2 = "0791534874894370" + "44" + "0C91534894847087" + "0000" + "52211131830580" + "0C" + "0500032A0202" + "AE6F399B0C"
	// Status report: MTI=2 in the first TPDU octet, no SMSC.
	pduStatusReport = "0006AA0B9153489484708700005211112183058052111121830580"
	// Captured UCS2 single-part ("Тест1") with corrupted variants below.
	pduUCS2       = "0791534874894370000C915348948470870008522111218305800A04220435044104420031"
	pduUCS2OddUDL = "0791534874894370000C91534894847087000852211121830580" + "09" + "04220435044104420031"
	pduUCS2BigUDL = "0791534874894370000C91534894847087000852211121830580" + "0C" + "04220435044104420031"
	pduBadDCS     = "0791534874894370000C91534894847087" + "0088" + "52211121830580" + "0A" + "04220435044104420031"
)

func TestParsePDU_AlphanumericSender(t *testing.T) {
	msg, err := ParsePDU(pduAlphaSender)
	if err != nil {
		t.Fatalf("ParsePDU() error = %v", err)
	}
	if msg.Sender != "Google" {
		t.Errorf("Sender = %q, want Google", msg.Sender)
	}
	if msg.Text != "Hello" {
		t.Errorf("Text = %q, want Hello", msg.Text)
	}
}

func TestParsePDU_GSM7MultipartWithUDH(t *testing.T) {
	part1, err := ParsePDU(pduGSM7Part1)
	if err != nil {
		t.Fatalf("part1 error = %v", err)
	}
	if !part1.IsMultipart || part1.RefKind != 8 || part1.MultipartRef != 42 ||
		part1.TotalParts != 2 || part1.PartNumber != 1 {
		t.Fatalf("part1 multipart info = %+v", part1)
	}
	if part1.Text != "Hello" {
		t.Errorf("part1 text = %q, want Hello (fill-bit decode)", part1.Text)
	}

	part2, err := ParsePDU(pduGSM7Part2)
	if err != nil {
		t.Fatalf("part2 error = %v", err)
	}
	if part2.Text != "World" {
		t.Errorf("part2 text = %q, want World", part2.Text)
	}

	collector := NewMultipartCollector()
	if got, _ := collector.Add(1, part1); got != nil {
		t.Fatal("incomplete multipart returned early")
	}
	assembled, indices := collector.Add(2, part2)
	if assembled == nil {
		t.Fatal("multipart not assembled")
	}
	if assembled.Text != "HelloWorld" {
		t.Errorf("assembled text = %q, want HelloWorld", assembled.Text)
	}
	if len(indices) != 2 {
		t.Errorf("indices = %v, want 2 entries", indices)
	}
}

func TestParsePDU_TypedErrors(t *testing.T) {
	t.Run("status report", func(t *testing.T) {
		_, err := ParsePDU(pduStatusReport)
		var nd *NotDeliverError
		if !errors.As(err, &nd) || nd.MTI != 2 {
			t.Fatalf("error = %v, want NotDeliverError MTI=2", err)
		}
	})

	t.Run("odd UCS2 UDL", func(t *testing.T) {
		_, err := ParsePDU(pduUCS2OddUDL)
		var mf *MalformedPDUError
		if !errors.As(err, &mf) {
			t.Fatalf("error = %v, want MalformedPDUError", err)
		}
	})

	t.Run("UDL beyond data", func(t *testing.T) {
		_, err := ParsePDU(pduUCS2BigUDL)
		var mf *MalformedPDUError
		if !errors.As(err, &mf) {
			t.Fatalf("error = %v, want MalformedPDUError", err)
		}
	})

	t.Run("reserved DCS", func(t *testing.T) {
		_, err := ParsePDU(pduBadDCS)
		var ue *UnsupportedEncodingError
		if !errors.As(err, &ue) {
			t.Fatalf("error = %v, want UnsupportedEncodingError", err)
		}
		if ue.Msg == nil || ue.Msg.Sender == "" {
			t.Error("unsupported-encoding error should carry sender metadata")
		}
	})

	t.Run("invalid hex", func(t *testing.T) {
		_, err := ParsePDU("ZZZZZZZZZZZZZZZZZZZZZZ")
		var mf *MalformedPDUError
		if !errors.As(err, &mf) {
			t.Fatalf("error = %v, want MalformedPDUError", err)
		}
	})
}

func TestDCSAlphabet(t *testing.T) {
	tests := []struct {
		dcs     byte
		want    int
		wantErr bool
	}{
		{0x00, 0, false}, // GSM7
		{0x04, 1, false}, // 8-bit
		{0x08, 2, false}, // UCS2
		{0x0C, 0, true},  // reserved alphabet
		{0x20, 0, true},  // compressed
		{0x88, 0, true},  // reserved group
		{0xC0, 0, false}, // MWI discard → GSM7
		{0xD8, 0, false}, // MWI store → GSM7
		{0xE0, 2, false}, // MWI store UCS2
		{0xF0, 0, false}, // data coding, GSM7
		{0xF4, 1, false}, // data coding, 8-bit
	}
	for _, tt := range tests {
		got, err := dcsAlphabet(tt.dcs)
		if (err != nil) != tt.wantErr {
			t.Errorf("dcsAlphabet(0x%02X) err = %v, wantErr %v", tt.dcs, err, tt.wantErr)
			continue
		}
		if err == nil && got != tt.want {
			t.Errorf("dcsAlphabet(0x%02X) = %d, want %d", tt.dcs, got, tt.want)
		}
	}
}

func TestDecodeSCTSInvalid(t *testing.T) {
	tests := []struct {
		name string
		data []byte
	}{
		{"non-BCD nibble", []byte{0x4F, 0x21, 0x11, 0x51, 0x03, 0x54, 0x21}},
		{"month zero", []byte{0x42, 0x00, 0x11, 0x51, 0x03, 0x54, 0x21}},
		{"month 13", []byte{0x42, 0x31, 0x11, 0x51, 0x03, 0x54, 0x21}},
		{"hour 24", []byte{0x42, 0x21, 0x11, 0x42, 0x03, 0x54, 0x21}},
		{"tz non-BCD", []byte{0x42, 0x21, 0x11, 0x51, 0x03, 0x54, 0xA0}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := decodeSCTS(tt.data); !got.IsZero() {
				t.Errorf("decodeSCTS(%x) = %v, want zero time", tt.data, got)
			}
		})
	}
}

func TestDecodeSCTSNegativeTimezone(t *testing.T) {
	// Same vector as TestDecodeSCTS but with the TZ sign bit set (UTC-3).
	data := []byte{0x42, 0x21, 0x11, 0x51, 0x03, 0x54, 0x29}
	got := decodeSCTS(data)
	_, offset := got.Zone()
	if offset != -10800 {
		t.Errorf("timezone offset = %d, want -10800", offset)
	}
}

func TestDecodeBCDDigitsExtension(t *testing.T) {
	// Nibbles: 1,2,*,#,a — 0x21, 0xBA (A=*,B=#... swapped: low first)
	// digits: low(0x21)=1, high=2, low(0xBA)=A(*), high=B(#)
	got := decodeBCDDigits([]byte{0x21, 0xBA}, 4)
	if got != "12*#" {
		t.Errorf("decodeBCDDigits = %q, want 12*#", got)
	}
	// Declared digit count truncates trailing garbage.
	got = decodeBCDDigits([]byte{0x21, 0x43}, 3)
	if got != "123" {
		t.Errorf("decodeBCDDigits = %q, want 123", got)
	}
}

func TestMultipartCollectorRefCollision(t *testing.T) {
	collector := NewMultipartCollector()
	base := func(total, part int) *PDUMessage {
		return &PDUMessage{
			Sender: "+111", IsMultipart: true, RefKind: 8,
			MultipartRef: 5, TotalParts: total, PartNumber: part,
			Text: "x", Timestamp: time.Now(),
		}
	}

	// Same sender+ref but different TotalParts → different logical messages.
	collector.Add(1, base(2, 1))
	collector.Add(2, base(3, 1))
	if collector.Pending() != 2 {
		t.Errorf("Pending() = %d, want 2 (different totals must not merge)", collector.Pending())
	}

	// Different reference width also separates.
	m := base(2, 1)
	m.RefKind = 16
	collector.Add(3, m)
	if collector.Pending() != 3 {
		t.Errorf("Pending() = %d, want 3 (different ref width must not merge)", collector.Pending())
	}
}

func TestMultipartCollectorIdenticalDuplicate(t *testing.T) {
	collector := NewMultipartCollector()
	part := func(idx, num int, text string) (int, *PDUMessage) {
		return idx, &PDUMessage{
			Sender: "+111", IsMultipart: true, RefKind: 8,
			MultipartRef: 7, TotalParts: 2, PartNumber: num, Text: text,
		}
	}

	collector.Add(part(10, 1, "A"))
	collector.Add(part(11, 1, "A")) // network delivered part 1 twice
	assembled, indices := collector.Add(part(12, 2, "B"))
	if assembled == nil {
		t.Fatal("duplicate part must not block assembly")
	}
	if assembled.Text != "AB" {
		t.Errorf("text = %q, want AB", assembled.Text)
	}
	if len(indices) != 3 {
		t.Errorf("indices = %v, want all three SIM slots for deletion", indices)
	}
}

func TestMultipartCollectorConflictingDuplicate(t *testing.T) {
	collector := NewMultipartCollector()
	part := func(idx, num int, text string) (int, *PDUMessage) {
		return idx, &PDUMessage{
			Sender: "+111", IsMultipart: true, RefKind: 8,
			MultipartRef: 7, TotalParts: 2, PartNumber: num, Text: text,
			Timestamp: time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC),
		}
	}

	collector.Add(part(10, 1, "A"))
	collector.Add(part(11, 1, "DIFFERENT")) // conflicting content
	assembled, _ := collector.Add(part(12, 2, "B"))
	if assembled != nil {
		t.Fatal("conflicting group must never assemble")
	}
	if len(collector.Conflicts()) != 1 {
		t.Errorf("Conflicts() = %v, want one entry", collector.Conflicts())
	}
	// Stale cleanup still reaches every slot of the conflicted group.
	stale := collector.StaleIndices(time.Hour, time.Date(2021, 1, 1, 0, 0, 0, 0, time.UTC))
	if len(stale) != 3 {
		t.Errorf("StaleIndices = %v, want all 3 conflicted slots", stale)
	}
}

func TestMultipartCollectorRelistedSlot(t *testing.T) {
	collector := NewMultipartCollector()
	msg := &PDUMessage{
		Sender: "+111", IsMultipart: true, RefKind: 8,
		MultipartRef: 7, TotalParts: 2, PartNumber: 1, Text: "A",
	}
	collector.Add(10, msg)
	collector.Add(10, msg) // same SIM slot listed again next poll
	if collector.Pending() != 1 {
		t.Errorf("Pending() = %d, want 1", collector.Pending())
	}
	if len(collector.Conflicts()) != 0 {
		t.Error("re-listing the same slot must not create a conflict")
	}
}

func FuzzParsePDU(f *testing.F) {
	f.Add(pduUCS2)
	f.Add(pduAlphaSender)
	f.Add(pduGSM7Part1)
	f.Add(pduStatusReport)
	f.Add(pduBadDCS)
	f.Add("0791534874894380400C915348948470870008522111811294808C0500030B0301041D043004470430043B043E000A0440044004400440")
	f.Add("00")
	f.Add("ZZ")

	f.Fuzz(func(t *testing.T, pduHex string) {
		msg, err := ParsePDU(pduHex) // must never panic
		if err == nil {
			if msg == nil {
				t.Fatal("nil message without error")
			}
			if msg.IsMultipart && (msg.PartNumber < 1 || msg.PartNumber > msg.TotalParts) {
				t.Fatalf("invalid multipart state: %+v", msg)
			}
		}
	})
}
