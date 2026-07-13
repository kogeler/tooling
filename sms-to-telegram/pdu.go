// Copyright © 2025 kogeler
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"encoding/hex"
	"fmt"
	"strings"
	"time"
	"unicode/utf16"
)

// Typed parse outcomes. The pipeline decides policy (forward / raw fallback /
// skip / delete) from the error type, never from error text.

// NotDeliverError: structurally valid TPDU that is not an SMS-DELIVER
// (stored SMS-SUBMIT, status report, reserved).
type NotDeliverError struct {
	MTI byte // 1 = SUBMIT, 2 = STATUS-REPORT/COMMAND, 3 = reserved
}

func (e *NotDeliverError) Error() string {
	return fmt.Sprintf("not an SMS-DELIVER message (MTI=%d)", e.MTI)
}

// MalformedPDUError: the PDU violates structural bounds and cannot be trusted.
type MalformedPDUError struct {
	Reason string
}

func (e *MalformedPDUError) Error() string { return "malformed PDU: " + e.Reason }

func malformed(format string, args ...any) *MalformedPDUError {
	return &MalformedPDUError{Reason: fmt.Sprintf(format, args...)}
}

// UnsupportedEncodingError: a structurally valid SMS-DELIVER whose text cannot
// be decoded (reserved/compressed DCS, national shift tables). Msg carries the
// decoded metadata (sender, timestamp) for a marked raw-PDU fallback.
type UnsupportedEncodingError struct {
	Reason string
	Msg    *PDUMessage
}

func (e *UnsupportedEncodingError) Error() string { return "unsupported encoding: " + e.Reason }

// PDUMessage represents a parsed SMS PDU
type PDUMessage struct {
	SMSC      string    // Service center number
	Sender    string    // Sender phone number or alphanumeric ID
	Timestamp time.Time // Message timestamp (zero when SCTS was invalid)
	Text      string    // Decoded message text
	Alphabet  int       // 0 = GSM7, 1 = 8-bit, 2 = UCS2
	// Multipart info
	IsMultipart  bool
	RefKind      int // 8 or 16 (bit reference width), 0 when not multipart
	MultipartRef int // Reference number for multipart
	PartNumber   int // Current part number (1-based)
	TotalParts   int // Total number of parts
}

// dcsAlphabet maps a TP-DCS octet to an alphabet per 3GPP TS 23.038 coding
// groups. Returns an error for compressed and reserved schemes.
func dcsAlphabet(dcs byte) (int, error) {
	switch {
	case dcs&0x80 == 0x00: // 00xx general / 01xx auto-deletion group
		if dcs&0x20 != 0 {
			return 0, fmt.Errorf("compressed text (DCS=0x%02X)", dcs)
		}
		alpha := int(dcs>>2) & 0x03
		if alpha == 3 {
			return 0, fmt.Errorf("reserved alphabet (DCS=0x%02X)", dcs)
		}
		return alpha, nil
	case dcs&0xF0 == 0xC0: // MWI discard: GSM7
		return 0, nil
	case dcs&0xF0 == 0xD0: // MWI store: GSM7
		return 0, nil
	case dcs&0xF0 == 0xE0: // MWI store: UCS2
		return 2, nil
	case dcs&0xF0 == 0xF0: // data coding / message class
		if dcs&0x04 != 0 {
			return 1, nil
		}
		return 0, nil
	default: // 0x80-0xBF reserved coding groups
		return 0, fmt.Errorf("reserved DCS coding group (DCS=0x%02X)", dcs)
	}
}

// ParsePDU parses a hex-encoded PDU string into a PDUMessage.
// Non-nil errors are typed: *NotDeliverError, *MalformedPDUError,
// *UnsupportedEncodingError.
func ParsePDU(pduHex string) (*PDUMessage, error) {
	data, err := hex.DecodeString(strings.TrimSpace(pduHex))
	if err != nil {
		return nil, malformed("invalid hex: %v", err)
	}

	if len(data) < 10 {
		return nil, malformed("PDU too short: %d bytes", len(data))
	}

	pos := 0
	msg := &PDUMessage{}

	// 1. Parse SMSC (Service Center Address)
	smscLen := int(data[pos])
	pos++
	if smscLen > 0 {
		if pos+smscLen > len(data) {
			return nil, malformed("SMSC length exceeds PDU")
		}
		// First byte is type, rest is number in swapped nibbles
		if smscLen > 1 {
			smscType := data[pos]
			isInternational := (smscType>>4)&0x07 == 0x01
			msg.SMSC = decodePhoneNumber(data[pos+1:pos+smscLen], isInternational)
		}
		pos += smscLen
	}

	// 2. PDU Type (first octet of TPDU)
	if pos >= len(data) {
		return nil, malformed("PDU too short for type")
	}
	pduType := data[pos]
	pos++

	// Bits 0-1: MTI (Message Type Indicator), 00 = SMS-DELIVER
	if mti := pduType & 0x03; mti != 0x00 {
		return nil, &NotDeliverError{MTI: mti}
	}

	// Check for User Data Header (bit 6)
	hasUDH := (pduType & 0x40) != 0

	// 3. Originating Address (sender)
	if pos >= len(data) {
		return nil, malformed("PDU too short for OA length")
	}
	oaLen := int(data[pos]) // Number of semi-octets (digits)
	pos++
	// 3GPP TS 23.040: address value is at most 10 octets (20 semi-octets);
	// alphanumeric addresses use at most 11 octets (22 semi-octets).
	if oaLen > 22 {
		return nil, malformed("OA length %d exceeds spec maximum", oaLen)
	}

	if pos >= len(data) {
		return nil, malformed("PDU too short for OA type")
	}
	oaType := data[pos]
	pos++

	oaBytes := (oaLen + 1) / 2
	if pos+oaBytes > len(data) {
		return nil, malformed("OA length exceeds PDU")
	}
	msg.Sender = decodeAddress(oaLen, oaType, data[pos:pos+oaBytes])
	pos += oaBytes

	// 4. Protocol Identifier (PID)
	if pos >= len(data) {
		return nil, malformed("PDU too short for PID")
	}
	pos++ // Skip PID

	// 5. Data Coding Scheme (DCS)
	if pos >= len(data) {
		return nil, malformed("PDU too short for DCS")
	}
	dcs := data[pos]
	pos++

	// 6. Service Centre Time Stamp (SCTS) - 7 bytes
	if pos+7 > len(data) {
		return nil, malformed("PDU too short for SCTS")
	}
	msg.Timestamp = decodeSCTS(data[pos : pos+7])
	pos += 7

	// 7. User Data Length (UDL)
	if pos >= len(data) {
		return nil, malformed("PDU too short for UDL")
	}
	udl := int(data[pos])
	pos++

	// 8. User Data (UD)
	userData := data[pos:]

	// Parse UDH if present
	udhLen := 0
	if hasUDH {
		if len(userData) == 0 {
			return nil, malformed("UDH indicated but user data empty")
		}
		udhLen = int(userData[0]) + 1 // +1 for the length byte itself
		if udhLen > len(userData) {
			return nil, malformed("UDH length exceeds user data")
		}

		info := parseUDHInfo(userData[1:udhLen])
		if info.malformed {
			return nil, malformed("invalid UDH: %s", info.malformedReason)
		}
		if info.unsupportedShift {
			return nil, &UnsupportedEncodingError{
				Reason: "national language shift table",
				Msg:    msg,
			}
		}
		if info.multipart {
			msg.IsMultipart = true
			msg.RefKind = info.refKind
			msg.MultipartRef = info.ref
			msg.TotalParts = info.total
			msg.PartNumber = info.part
		}
		userData = userData[udhLen:]
	}

	// Determine encoding from DCS (after UDH so metadata is available for the
	// unsupported-encoding fallback).
	alphabet, dcsErr := dcsAlphabet(dcs)
	if dcsErr != nil {
		return nil, &UnsupportedEncodingError{Reason: dcsErr.Error(), Msg: msg}
	}
	msg.Alphabet = alphabet

	// Decode message text with strict UDL/UDH bounds.
	switch alphabet {
	case 0: // GSM 7-bit: UDL counts septets including the UDH
		udhSeptets := 0
		fillBits := 0
		if hasUDH {
			udhSeptets = (udhLen*8 + 6) / 7
			fillBits = (7 - ((udhLen * 8) % 7)) % 7
		}
		textSeptets := udl - udhSeptets
		if textSeptets < 0 {
			return nil, malformed("UDL %d smaller than UDH septets %d", udl, udhSeptets)
		}
		availableSeptets := (len(userData)*8 - fillBits) / 7
		if availableSeptets < textSeptets {
			return nil, malformed("user data holds %d septets, UDL requires %d", availableSeptets, textSeptets)
		}
		msg.Text = decodeGSM7Bit(userData, textSeptets, fillBits)
	case 1, 2: // 8-bit and UCS2: UDL counts octets including the UDH
		textBytes := udl - udhLen
		if textBytes < 0 {
			return nil, malformed("UDL %d smaller than UDH length %d", udl, udhLen)
		}
		if len(userData) < textBytes {
			return nil, malformed("user data holds %d bytes, UDL requires %d", len(userData), textBytes)
		}
		payload := userData[:textBytes]
		if alphabet == 2 {
			if textBytes%2 != 0 {
				return nil, malformed("UCS2 payload length %d is odd", textBytes)
			}
			msg.Text = decodeUCS2(payload)
		} else {
			msg.Text = string(payload)
		}
	}

	return msg, nil
}

// udhInfo is the validated result of parsing a User Data Header.
type udhInfo struct {
	multipart        bool
	refKind          int // 8 or 16
	ref, total, part int
	malformed        bool
	malformedReason  string
	unsupportedShift bool
}

// parseUDHInfo parses and validates a User Data Header (without its leading
// length byte). Concatenation IEs are validated strictly: exact IE length,
// total >= 1 and 1 <= part <= total; conflicting duplicates are malformed.
func parseUDHInfo(udh []byte) udhInfo {
	var info udhInfo
	bad := func(format string, args ...any) udhInfo {
		info.malformed = true
		info.malformedReason = fmt.Sprintf(format, args...)
		return info
	}

	pos := 0
	for pos < len(udh) {
		if pos+2 > len(udh) {
			return bad("truncated IE header at offset %d", pos)
		}
		iei := udh[pos]
		iel := int(udh[pos+1])
		pos += 2

		if pos+iel > len(udh) {
			return bad("IE 0x%02X length %d exceeds UDH", iei, iel)
		}
		ieData := udh[pos : pos+iel]
		pos += iel

		var refKind, ref, total, part int
		switch iei {
		case 0x00: // Concatenated SMS, 8-bit reference
			if iel != 3 {
				return bad("concat IE 0x00 length %d, want 3", iel)
			}
			refKind, ref, total, part = 8, int(ieData[0]), int(ieData[1]), int(ieData[2])
		case 0x08: // Concatenated SMS, 16-bit reference
			if iel != 4 {
				return bad("concat IE 0x08 length %d, want 4", iel)
			}
			refKind, ref, total, part = 16, int(ieData[0])<<8|int(ieData[1]), int(ieData[2]), int(ieData[3])
		case 0x24, 0x25: // National language shift tables: text would decode wrong
			info.unsupportedShift = true
			continue
		default:
			continue // other IEs are irrelevant here
		}

		if total < 1 || part < 1 || part > total {
			return bad("concat counters part=%d total=%d", part, total)
		}
		if info.multipart && (info.refKind != refKind || info.ref != ref || info.total != total || info.part != part) {
			return bad("conflicting concat IEs")
		}
		info.multipart = true
		info.refKind = refKind
		info.ref = ref
		info.total = total
		info.part = part
	}

	return info
}

// decodeAddress decodes an originating/destination address honoring the
// type-of-number: alphanumeric addresses (TON 0b101) are GSM 7-bit packed
// text, everything else is swapped-nibble BCD limited to the declared number
// of semi-octets.
func decodeAddress(addrLen int, addrType byte, data []byte) string {
	ton := (addrType >> 4) & 0x07
	if ton == 0x05 { // alphanumeric
		septets := addrLen * 4 / 7
		return decodeGSM7Bit(data, septets, 0)
	}
	num := decodeBCDDigits(data, addrLen)
	if ton == 0x01 { // international
		return "+" + num
	}
	return num
}

// bcdDigits maps BCD nibbles to characters per TS 23.040 (0xA-0xE are the
// extension digits, 0xF is the filler).
const bcdDigits = "0123456789*#abc"

// decodeBCDDigits decodes up to digitCount swapped-nibble BCD digits.
func decodeBCDDigits(data []byte, digitCount int) string {
	var b strings.Builder
	count := 0
	for _, octet := range data {
		for _, nib := range [2]byte{octet & 0x0F, octet >> 4} {
			if count >= digitCount {
				break
			}
			count++
			if nib == 0x0F {
				continue // filler
			}
			b.WriteByte(bcdDigits[nib])
		}
	}
	return b.String()
}

// decodePhoneNumber decodes a phone number from swapped nibbles format
// (used for the SMSC address, whose length is given in octets).
func decodePhoneNumber(data []byte, international bool) string {
	num := decodeBCDDigits(data, len(data)*2)
	if international {
		return "+" + num
	}
	return num
}

// decodeSCTS decodes a Service Centre Time Stamp. Invalid BCD or out-of-range
// calendar fields yield the zero time: a garbage timestamp must never feed
// stale-part cleanup or be presented as real.
func decodeSCTS(data []byte) time.Time {
	if len(data) < 7 {
		return time.Time{}
	}

	// Each byte is BCD with swapped nibbles.
	decodeBCD := func(b byte) (int, bool) {
		lo, hi := int(b&0x0F), int(b>>4)
		if lo > 9 || hi > 9 {
			return 0, false
		}
		return lo*10 + hi, true
	}

	var vals [6]int
	for i := 0; i < 6; i++ {
		v, ok := decodeBCD(data[i])
		if !ok {
			return time.Time{}
		}
		vals[i] = v
	}
	year, month, day := 2000+vals[0], vals[1], vals[2]
	hour, minute, second := vals[3], vals[4], vals[5]
	if month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59 || second > 59 {
		return time.Time{}
	}

	// Timezone in quarter hours; sign is bit 3 of the low nibble (the first
	// transmitted semi-octet).
	tz := data[6]
	tzSign := 1
	if tz&0x08 != 0 {
		tzSign = -1
		tz &= 0xF7
	}
	tzQuarters, ok := decodeBCD(tz)
	if !ok || tzQuarters > 79 { // spec range: 0..79 quarter hours
		return time.Time{}
	}
	tzOffset := tzSign * tzQuarters * 15 * 60 // seconds

	loc := time.FixedZone("", tzOffset)
	return time.Date(year, time.Month(month), day, hour, minute, second, 0, loc)
}

// GSM 7-bit default alphabet
var gsm7BitDefault = []rune{
	'@', '£', '$', '¥', 'è', 'é', 'ù', 'ì', 'ò', 'Ç', '\n', 'Ø', 'ø', '\r', 'Å', 'å',
	'Δ', '_', 'Φ', 'Γ', 'Λ', 'Ω', 'Π', 'Ψ', 'Σ', 'Θ', 'Ξ', '\x1b', 'Æ', 'æ', 'ß', 'É',
	' ', '!', '"', '#', '¤', '%', '&', '\'', '(', ')', '*', '+', ',', '-', '.', '/',
	'0', '1', '2', '3', '4', '5', '6', '7', '8', '9', ':', ';', '<', '=', '>', '?',
	'¡', 'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O',
	'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', 'Ä', 'Ö', 'Ñ', 'Ü', '§',
	'¿', 'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o',
	'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z', 'ä', 'ö', 'ñ', 'ü', 'à',
}

// GSM 7-bit extension table (accessed via escape 0x1B)
var gsm7BitExtension = map[byte]rune{
	0x0A: '\f',
	0x14: '^',
	0x28: '{',
	0x29: '}',
	0x2F: '\\',
	0x3C: '[',
	0x3D: '~',
	0x3E: ']',
	0x40: '|',
	0x65: '€',
}

// decodeGSM7Bit decodes GSM 7-bit packed data
func decodeGSM7Bit(data []byte, numChars int, fillBits int) string {
	if len(data) == 0 || numChars <= 0 {
		return ""
	}

	// Unpack 7-bit characters from 8-bit bytes
	var septets []byte
	var currentByte int
	var bitsInBuffer int

	// Skip fill bits at start
	bitPos := fillBits

	for len(septets) < numChars && (bitPos/8) < len(data) {
		byteIdx := bitPos / 8
		bitOffset := bitPos % 8

		// Get 7 bits starting at bitPos
		currentByte = int(data[byteIdx]) >> bitOffset
		bitsInBuffer = 8 - bitOffset

		if bitsInBuffer < 7 && byteIdx+1 < len(data) {
			currentByte |= int(data[byteIdx+1]) << bitsInBuffer
		}

		septet := byte(currentByte & 0x7F)
		septets = append(septets, septet)
		bitPos += 7
	}

	// Convert septets to string
	var result []rune
	escape := false
	for _, s := range septets {
		if s == 0x1B {
			escape = true
			continue
		}

		if escape {
			if r, ok := gsm7BitExtension[s]; ok {
				result = append(result, r)
			} else {
				result = append(result, ' ')
			}
			escape = false
		} else {
			if int(s) < len(gsm7BitDefault) {
				result = append(result, gsm7BitDefault[s])
			} else {
				result = append(result, '?')
			}
		}
	}

	return string(result)
}

// decodeUCS2 decodes UCS-2 (UTF-16BE) encoded data
func decodeUCS2(data []byte) string {
	if len(data) < 2 {
		return ""
	}

	// Convert bytes to uint16 (big-endian)
	var u16 []uint16
	for i := 0; i+1 < len(data); i += 2 {
		u16 = append(u16, uint16(data[i])<<8|uint16(data[i+1]))
	}

	// Decode UTF-16 to runes
	runes := utf16.Decode(u16)
	return string(runes)
}

// multipartKey identifies one logical concatenated message. Reference width,
// total count and alphabet are part of the identity: an 8-bit reference wraps
// after 256 messages, and grouping on sender+ref alone can splice parts of
// different messages together.
type multipartKey struct {
	sender   string
	refKind  int
	ref      int
	total    int
	alphabet int
}

type multipartPart struct {
	index int
	msg   *PDUMessage
}

type multipartGroup struct {
	// parts maps part number -> entries. Byte-identical duplicate deliveries
	// append extra entries so every SIM slot is deleted with the assembly.
	parts    map[int][]multipartPart
	conflict bool
}

// MultipartCollector collects parts of multipart SMS messages
type MultipartCollector struct {
	groups map[multipartKey]*multipartGroup
}

// NewMultipartCollector creates a new collector
func NewMultipartCollector() *MultipartCollector {
	return &MultipartCollector{
		groups: make(map[multipartKey]*multipartGroup),
	}
}

func keyFor(msg *PDUMessage) multipartKey {
	return multipartKey{
		sender:   msg.Sender,
		refKind:  msg.RefKind,
		ref:      msg.MultipartRef,
		total:    msg.TotalParts,
		alphabet: msg.Alphabet,
	}
}

// Add adds a message part and returns the complete message plus all part SIM
// indices once every part is present. Groups with conflicting duplicate parts
// are never assembled (and never deleted here); stale cleanup resolves them.
func (c *MultipartCollector) Add(index int, msg *PDUMessage) (*PDUMessage, []int) {
	if !msg.IsMultipart {
		return msg, []int{index}
	}

	key := keyFor(msg)
	group := c.groups[key]
	if group == nil {
		group = &multipartGroup{parts: make(map[int][]multipartPart)}
		c.groups[key] = group
	}

	entries := group.parts[msg.PartNumber]
	for _, e := range entries {
		if e.index == index {
			return nil, nil // same SIM slot re-listed; nothing new
		}
	}
	if len(entries) > 0 && entries[0].msg.Text != msg.Text {
		// Same part number, different content: cannot know which is right.
		if !group.conflict {
			group.conflict = true
		}
	}
	group.parts[msg.PartNumber] = append(entries, multipartPart{index: index, msg: msg})

	if group.conflict {
		return nil, nil
	}

	// Part numbers are validated to 1..total, so having `total` distinct part
	// numbers means the message is complete.
	if len(group.parts) != msg.TotalParts {
		return nil, nil
	}

	firstPart := group.parts[1][0]
	var fullText strings.Builder
	var indices []int
	for i := 1; i <= msg.TotalParts; i++ {
		entries := group.parts[i]
		fullText.WriteString(entries[0].msg.Text)
		for _, e := range entries {
			indices = append(indices, e.index)
		}
	}

	delete(c.groups, key)

	return &PDUMessage{
		Sender:       firstPart.msg.Sender,
		Timestamp:    firstPart.msg.Timestamp,
		Text:         fullText.String(),
		SMSC:         firstPart.msg.SMSC,
		Alphabet:     firstPart.msg.Alphabet,
		IsMultipart:  true,
		RefKind:      msg.RefKind,
		MultipartRef: msg.MultipartRef,
		PartNumber:   1,
		TotalParts:   msg.TotalParts,
	}, indices
}

// Pending returns number of incomplete multipart messages
func (c *MultipartCollector) Pending() int {
	return len(c.groups)
}

// Conflicts describes groups with conflicting duplicate parts.
func (c *MultipartCollector) Conflicts() []string {
	var out []string
	for key, group := range c.groups {
		if group.conflict {
			out = append(out, fmt.Sprintf("sender=%s ref=%d total=%d", key.sender, key.ref, key.total))
		}
	}
	return out
}

// MaxPendingTotalParts returns the largest declared TotalParts among pending
// groups (0 when none) — used to warn when a message cannot fit SIM storage.
func (c *MultipartCollector) MaxPendingTotalParts() int {
	max := 0
	for key := range c.groups {
		if key.total > max {
			max = key.total
		}
	}
	return max
}

// StaleIndices returns indices of multipart parts older than maxAge.
// Parts with an invalid (zero) timestamp are never considered stale.
func (c *MultipartCollector) StaleIndices(maxAge time.Duration, now time.Time) []int {
	if maxAge <= 0 {
		return nil
	}

	var stale []int
	for _, group := range c.groups {
		for _, entries := range group.parts {
			for _, part := range entries {
				ts := part.msg.Timestamp
				if ts.IsZero() {
					continue
				}
				age := now.Sub(ts)
				if age < 0 {
					continue
				}
				if age >= maxAge {
					stale = append(stale, part.index)
				}
			}
		}
	}

	return stale
}
