// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

// SMS-SUBMIT PDU encoding used by the live loopback suite (live_test.go) to
// send SMS through the real modem via AT+CMGS. It lives in an untagged test
// file so it is compiled, vetted and unit-tested on every ordinary `go test`
// run; the hardware-touching scenarios are behind the `live` build tag.

package main

import (
	"encoding/hex"
	"fmt"
	"strings"
	"testing"
	"unicode/utf16"
)

// gsm7Reverse maps runes back to GSM 7-bit default-alphabet septets.
var gsm7Reverse = func() map[rune]byte {
	m := make(map[rune]byte, len(gsm7BitDefault))
	for i, r := range gsm7BitDefault {
		if r == '\x1b' {
			continue
		}
		m[r] = byte(i)
	}
	return m
}()

// gsm7ReverseExt maps extension-table runes to their escaped septet code.
var gsm7ReverseExt = func() map[rune]byte {
	m := make(map[rune]byte, len(gsm7BitExtension))
	for code, r := range gsm7BitExtension {
		m[r] = code
	}
	return m
}()

// gsm7Septets converts text to septet values; extension-table characters
// (€ [ ] { } ~ \ | ^ \f) become 0x1B escape pairs and count as two septets.
func gsm7Septets(s string) ([]byte, error) {
	septets := make([]byte, 0, len(s))
	for _, r := range s {
		if v, ok := gsm7Reverse[r]; ok {
			septets = append(septets, v)
			continue
		}
		if code, ok := gsm7ReverseExt[r]; ok {
			septets = append(septets, 0x1B, code)
			continue
		}
		return nil, fmt.Errorf("rune %q not in GSM 7-bit alphabet", r)
	}
	return septets, nil
}

// packGSM7 packs septets LSB-first with the given number of leading fill bits
// (the exact inverse of decodeGSM7Bit).
func packGSM7(septets []byte, fillBits int) []byte {
	totalBits := fillBits + 7*len(septets)
	out := make([]byte, (totalBits+7)/8)
	bitPos := fillBits
	for _, s := range septets {
		idx, off := bitPos/8, bitPos%8
		v := uint16(s) << off
		out[idx] |= byte(v)
		if off > 1 && idx+1 < len(out) {
			out[idx+1] |= byte(v >> 8)
		}
		bitPos += 7
	}
	return out
}

// encodeUCS2 renders text as UTF-16BE bytes.
func encodeUCS2(s string) []byte {
	u16 := utf16.Encode([]rune(s))
	out := make([]byte, 0, len(u16)*2)
	for _, u := range u16 {
		out = append(out, byte(u>>8), byte(u))
	}
	return out
}

// encodeBCDNumber renders digits as swapped-nibble BCD with F padding.
func encodeBCDNumber(digits string) ([]byte, error) {
	out := make([]byte, 0, (len(digits)+1)/2)
	for i := 0; i < len(digits); i += 2 {
		lo := digits[i]
		if lo < '0' || lo > '9' {
			return nil, fmt.Errorf("non-digit %q in number", lo)
		}
		hi := byte(0x0F)
		if i+1 < len(digits) {
			c := digits[i+1]
			if c < '0' || c > '9' {
				return nil, fmt.Errorf("non-digit %q in number", c)
			}
			hi = c - '0'
		}
		out = append(out, hi<<4|(lo-'0'))
	}
	return out, nil
}

// concatRef describes one part of a concatenated message (8-bit reference).
type concatRef struct {
	ref, total, part int
}

// encodeSubmitPDU builds a complete SMS-SUBMIT PDU (with a zero-length SMSC
// field: the SIM's default SMSC is used) and returns the hex string plus the
// TPDU length for AT+CMGS=<n>.
func encodeSubmitPDU(dest, body string, ucs2 bool, concat *concatRef) (string, int, error) {
	digits := strings.TrimPrefix(dest, "+")
	if digits == "" {
		return "", 0, fmt.Errorf("empty destination")
	}
	daBytes, err := encodeBCDNumber(digits)
	if err != nil {
		return "", 0, err
	}

	firstOctet := byte(0x01) // SMS-SUBMIT, no validity period
	var udh []byte
	if concat != nil {
		firstOctet |= 0x40 // TP-UDHI
		udh = []byte{0x05, 0x00, 0x03, byte(concat.ref), byte(concat.total), byte(concat.part)}
	}

	var udl byte
	var ud []byte
	if ucs2 {
		payload := encodeUCS2(body)
		udl = byte(len(udh) + len(payload))
		ud = append(udh, payload...)
	} else {
		septets, err := gsm7Septets(body)
		if err != nil {
			return "", 0, err
		}
		fillBits := 0
		udhSeptets := 0
		if len(udh) > 0 {
			udhSeptets = (len(udh)*8 + 6) / 7
			fillBits = (7 - (len(udh)*8)%7) % 7
		}
		udl = byte(udhSeptets + len(septets))
		ud = append(udh, packGSM7(septets, fillBits)...)
	}

	tpdu := []byte{firstOctet, 0x00 /* TP-MR: modem assigns */}
	tpdu = append(tpdu, byte(len(digits)), 0x91 /* international */)
	tpdu = append(tpdu, daBytes...)
	tpdu = append(tpdu, 0x00 /* PID */)
	if ucs2 {
		tpdu = append(tpdu, 0x08)
	} else {
		tpdu = append(tpdu, 0x00)
	}
	tpdu = append(tpdu, udl)
	tpdu = append(tpdu, ud...)

	return "00" + strings.ToUpper(hex.EncodeToString(tpdu)), len(tpdu), nil
}

// --- Encoder unit tests (run on every ordinary go test) -----------------------

func TestPackGSM7_KnownVectors(t *testing.T) {
	septets, err := gsm7Septets("Hello")
	if err != nil {
		t.Fatal(err)
	}
	if got := hex.EncodeToString(packGSM7(septets, 0)); got != "c8329bfd06" {
		t.Errorf("packGSM7(Hello, 0) = %s, want c8329bfd06", got)
	}
	// One fill bit — the layout produced by a 6-byte concat UDH.
	if got := hex.EncodeToString(packGSM7(septets, 1)); got != "906536fb0d" {
		t.Errorf("packGSM7(Hello, 1) = %s, want 906536fb0d", got)
	}
}

func TestPackGSM7_RoundTrip(t *testing.T) {
	texts := []string{"Hello", "LIVE-12345 test body", "a", "0123456789 :;<=>?"}
	for _, text := range texts {
		for fill := 0; fill < 7; fill++ {
			septets, err := gsm7Septets(text)
			if err != nil {
				t.Fatalf("gsm7Septets(%q): %v", text, err)
			}
			packed := packGSM7(septets, fill)
			if got := decodeGSM7Bit(packed, len(septets), fill); got != text {
				t.Errorf("round trip %q fill=%d → %q", text, fill, got)
			}
		}
	}
}

func TestEncodeUCS2_RoundTrip(t *testing.T) {
	for _, text := range []string{"Привет", "Тест 123", "emoji 😀 ok"} {
		if got := decodeUCS2(encodeUCS2(text)); got != text {
			t.Errorf("UCS2 round trip %q → %q", text, got)
		}
	}
}

func TestEncodeBCDNumber_RoundTrip(t *testing.T) {
	for _, num := range []string{"79991234567", "1234567890", "123"} {
		encoded, err := encodeBCDNumber(num)
		if err != nil {
			t.Fatal(err)
		}
		if got := decodeBCDDigits(encoded, len(num)); got != num {
			t.Errorf("BCD round trip %q → %q", num, got)
		}
	}
	if _, err := encodeBCDNumber("12a4"); err == nil {
		t.Error("non-digit must be rejected")
	}
}

func TestEncodeSubmitPDU_Structure(t *testing.T) {
	pduHex, tpduLen, err := encodeSubmitPDU("+79991234567", "Hello", false, nil)
	if err != nil {
		t.Fatal(err)
	}
	data, err := hex.DecodeString(pduHex)
	if err != nil {
		t.Fatalf("not hex: %v", err)
	}
	if data[0] != 0x00 {
		t.Error("SMSC length must be 0 (SIM default)")
	}
	if tpduLen != len(data)-1 {
		t.Errorf("tpduLen = %d, want %d", tpduLen, len(data)-1)
	}
	if data[1] != 0x01 {
		t.Errorf("first octet = 0x%02X, want 0x01 (SUBMIT)", data[1])
	}
	if data[3] != 11 || data[4] != 0x91 {
		t.Errorf("DA header = %d/0x%02X, want 11/0x91", data[3], data[4])
	}
	// UD: last 5 bytes are packed "Hello".
	if got := hex.EncodeToString(data[len(data)-5:]); got != "c8329bfd06" {
		t.Errorf("UD = %s, want packed Hello", got)
	}
}

func TestEncodeSubmitPDU_MultipartUDH(t *testing.T) {
	pduHex, _, err := encodeSubmitPDU("+79991234567", "Hello", false, &concatRef{ref: 42, total: 3, part: 2})
	if err != nil {
		t.Fatal(err)
	}
	data, _ := hex.DecodeString(pduHex)
	if data[1]&0x40 == 0 {
		t.Fatal("TP-UDHI must be set for multipart")
	}
	// Locate the UDH: it follows firstOctet, MR, DA len, DA type, 6 DA bytes,
	// PID, DCS, UDL → offset 1+2+2+6+2+1 = 14.
	udh := data[15 : 15+5]
	info := parseUDHInfo(udh)
	if info.malformed || !info.multipart || info.ref != 42 || info.total != 3 || info.part != 2 {
		t.Errorf("UDH round trip failed: %+v (udh=%x)", info, udh)
	}
	// And the text after the UDH still decodes with one fill bit.
	if got := decodeGSM7Bit(data[20:], 5, 1); got != "Hello" {
		t.Errorf("body after UDH = %q, want Hello", got)
	}
}

func TestEncodeSubmitPDU_UCS2(t *testing.T) {
	body := "Тест1"
	pduHex, _, err := encodeSubmitPDU("+79991234567", body, true, nil)
	if err != nil {
		t.Fatal(err)
	}
	data, _ := hex.DecodeString(pduHex)
	// DCS at offset 12 (after SMSC len, first octet, MR, DA hdr+6, PID).
	if data[12] != 0x08 {
		t.Errorf("DCS = 0x%02X, want 0x08 (UCS2)", data[12])
	}
	udl := int(data[13])
	if got := decodeUCS2(data[14 : 14+udl]); got != body {
		t.Errorf("UCS2 body round trip → %q", got)
	}
}

// encodeDeliverPDU builds an SMS-DELIVER PDU exactly as a modem would list it
// (zero-length SMSC, fixed SCTS), so encoder->ParsePDU round trips exercise
// the full production parse path.
func encodeDeliverPDU(sender, body string, ucs2 bool, concat *concatRef) (string, error) {
	digits := strings.TrimPrefix(sender, "+")
	oaBytes, err := encodeBCDNumber(digits)
	if err != nil {
		return "", err
	}

	firstOctet := byte(0x04) // SMS-DELIVER, no more messages
	var udh []byte
	if concat != nil {
		firstOctet |= 0x40
		udh = []byte{0x05, 0x00, 0x03, byte(concat.ref), byte(concat.total), byte(concat.part)}
	}

	var udl byte
	var ud []byte
	if ucs2 {
		payload := encodeUCS2(body)
		udl = byte(len(udh) + len(payload))
		ud = append(udh, payload...)
	} else {
		septets, err := gsm7Septets(body)
		if err != nil {
			return "", err
		}
		fillBits, udhSeptets := 0, 0
		if len(udh) > 0 {
			udhSeptets = (len(udh)*8 + 6) / 7
			fillBits = (7 - (len(udh)*8)%7) % 7
		}
		udl = byte(udhSeptets + len(septets))
		ud = append(udh, packGSM7(septets, fillBits)...)
	}

	tpdu := []byte{firstOctet, byte(len(digits)), 0x91}
	tpdu = append(tpdu, oaBytes...)
	tpdu = append(tpdu, 0x00) // PID
	if ucs2 {
		tpdu = append(tpdu, 0x08)
	} else {
		tpdu = append(tpdu, 0x00)
	}
	tpdu = append(tpdu, 0x52, 0x21, 0x11, 0x21, 0x83, 0x05, 0x80) // SCTS
	tpdu = append(tpdu, udl)
	tpdu = append(tpdu, ud...)

	return "00" + strings.ToUpper(hex.EncodeToString(tpdu)), nil
}

// deliverRoundTrip encodes a DELIVER PDU and parses it back with the
// production parser, asserting byte-exact text.
func deliverRoundTrip(t *testing.T, body string, ucs2 bool, concat *concatRef) *PDUMessage {
	t.Helper()
	// Documentation-range number (RFC-style, never assigned to a subscriber).
	pduHex, err := encodeDeliverPDU("+15550001234", body, ucs2, concat)
	if err != nil {
		t.Fatalf("encodeDeliverPDU(%q): %v", body, err)
	}
	msg, err := ParsePDU(pduHex)
	if err != nil {
		t.Fatalf("ParsePDU(%q body): %v", body, err)
	}
	if msg.Text != body {
		t.Errorf("round trip %q -> %q", body, msg.Text)
	}
	return msg
}

// GSM7 escape sequences (0x1B pairs) through the full parser - previously the
// only untested decoder path.
func TestParsePDU_GSM7EscapeSequences(t *testing.T) {
	bodies := []string{
		"price is 100€",
		"a[b]c{d}e~f|g\\h^i",
		"€",
		"[[[]]]",
	}
	for _, body := range bodies {
		deliverRoundTrip(t, body, false, nil)
	}
}

// '@' is septet 0x00 and must survive anywhere, including the very end
// (where naive decoders confuse it with padding).
func TestParsePDU_GSM7AtSign(t *testing.T) {
	for _, body := range []string{"@", "user@host", "ends with @", "@@@@@@@"} {
		deliverRoundTrip(t, body, false, nil)
	}
}

// National characters of the GSM7 default alphabet (no escapes involved).
func TestParsePDU_GSM7NationalChars(t *testing.T) {
	deliverRoundTrip(t, "ä ö å Ä Ö Å é ü ñ ß à è", false, nil)
}

// Boundary lengths: 160 septets in a single part, 153 + 7-septet UDH in a
// concat part (both give UDL=160), 70 UCS2 chars.
func TestParsePDU_BoundaryLengths(t *testing.T) {
	body160 := strings.Repeat("A", 160)
	msg := deliverRoundTrip(t, body160, false, nil)
	if len(msg.Text) != 160 {
		t.Errorf("single-part length = %d, want 160", len(msg.Text))
	}

	body153 := strings.Repeat("B", 153)
	msg = deliverRoundTrip(t, body153, false, &concatRef{ref: 1, total: 2, part: 1})
	if !msg.IsMultipart || len(msg.Text) != 153 {
		t.Errorf("concat part: multipart=%v len=%d, want true/153", msg.IsMultipart, len(msg.Text))
	}

	body70 := strings.Repeat("ЖА", 35) // 70 UCS2 code units
	deliverRoundTrip(t, body70, true, nil)
}

// Empty user data (UDL=0) must parse to an empty text, not an error.
func TestParsePDU_EmptyBody(t *testing.T) {
	deliverRoundTrip(t, "", false, nil)
	deliverRoundTrip(t, "", true, nil)
}

// A dangling escape septet at the end of the stream is dropped silently -
// documented current behavior (the spec suggests rendering a space; losing
// the lone escape is acceptable, corrupting the preceding text is not).
func TestDecodeGSM7Bit_TrailingEscape(t *testing.T) {
	septets, err := gsm7Septets("ab")
	if err != nil {
		t.Fatal(err)
	}
	packed := packGSM7(append(septets, 0x1B), 0)
	if got := decodeGSM7Bit(packed, 3, 0); got != "ab" {
		t.Errorf("trailing escape decode = %q, want %q (escape dropped)", got, "ab")
	}
}

// Escape pairs survive packing at every fill-bit offset.
func TestPackGSM7_EscapeRoundTrip(t *testing.T) {
	for _, text := range []string{"€uro", "x[y]z", "a|b~c", "^start", "end€"} {
		septets, err := gsm7Septets(text)
		if err != nil {
			t.Fatalf("gsm7Septets(%q): %v", text, err)
		}
		for fill := 0; fill < 7; fill++ {
			if got := decodeGSM7Bit(packGSM7(septets, fill), len(septets), fill); got != text {
				t.Errorf("escape round trip %q fill=%d -> %q", text, fill, got)
			}
		}
	}
}

// Non-Latin scripts through UCS2: RTL (Arabic, Hebrew), CJK, mixed.
func TestParsePDU_UCS2Scripts(t *testing.T) {
	bodies := []string{
		"مرحبا بالعالم",
		"שלום עולם",
		"你好，世界",
		"日本語テスト",
		"mixed: кириллица مرحبا 你好 äöå",
	}
	for _, body := range bodies {
		deliverRoundTrip(t, body, true, nil)
	}
}
