package main

import (
	"encoding/hex"
	"fmt"
	"time"
	"unicode/utf16"
)

// PDUMessage represents a parsed SMS PDU
type PDUMessage struct {
	SMSC      string    // Service center number
	Sender    string    // Sender phone number
	Timestamp time.Time // Message timestamp
	Text      string    // Decoded message text
	// Multipart info
	IsMultipart  bool
	MultipartRef int // Reference number for multipart
	PartNumber   int // Current part number (1-based)
	TotalParts   int // Total number of parts
}

// ParsePDU parses a hex-encoded PDU string into a PDUMessage
func ParsePDU(pduHex string) (*PDUMessage, error) {
	data, err := hex.DecodeString(pduHex)
	if err != nil {
		return nil, fmt.Errorf("invalid hex: %w", err)
	}

	if len(data) < 10 {
		return nil, fmt.Errorf("PDU too short: %d bytes", len(data))
	}

	pos := 0
	msg := &PDUMessage{}

	// 1. Parse SMSC (Service Center Address)
	smscLen := int(data[pos])
	pos++
	if smscLen > 0 {
		if pos+smscLen > len(data) {
			return nil, fmt.Errorf("SMSC length exceeds PDU")
		}
		// First byte is type, rest is number in swapped nibbles
		if smscLen > 1 {
			smscType := data[pos]
			isInternational := (smscType & 0x70) == 0x10 // Type 0x91
			msg.SMSC = decodePhoneNumber(data[pos+1:pos+smscLen], isInternational)
		}
		pos += smscLen
	}

	// 2. PDU Type (first octet of TPDU)
	if pos >= len(data) {
		return nil, fmt.Errorf("PDU too short for type")
	}
	pduType := data[pos]
	pos++

	// Check if it's SMS-DELIVER (incoming SMS)
	// Bits 0-1: MTI (Message Type Indicator), 00 = SMS-DELIVER
	mti := pduType & 0x03
	if mti != 0x00 {
		return nil, fmt.Errorf("not an SMS-DELIVER message (MTI=%d)", mti)
	}

	// Check for User Data Header (bit 6)
	hasUDH := (pduType & 0x40) != 0

	// 3. Originating Address (sender)
	if pos >= len(data) {
		return nil, fmt.Errorf("PDU too short for OA length")
	}
	oaLen := int(data[pos]) // Number of digits (not bytes)
	pos++

	if pos >= len(data) {
		return nil, fmt.Errorf("PDU too short for OA type")
	}
	oaType := data[pos]
	pos++

	// Calculate bytes needed for OA (each byte = 2 digits, rounded up)
	oaBytes := (oaLen + 1) / 2
	if pos+oaBytes > len(data) {
		return nil, fmt.Errorf("OA length exceeds PDU")
	}

	isInternational := (oaType & 0x70) == 0x10
	msg.Sender = decodePhoneNumber(data[pos:pos+oaBytes], isInternational)
	pos += oaBytes

	// 4. Protocol Identifier (PID)
	if pos >= len(data) {
		return nil, fmt.Errorf("PDU too short for PID")
	}
	pos++ // Skip PID

	// 5. Data Coding Scheme (DCS)
	if pos >= len(data) {
		return nil, fmt.Errorf("PDU too short for DCS")
	}
	dcs := data[pos]
	pos++

	// Determine encoding from DCS
	// Bits 2-3 of DCS indicate alphabet:
	// 00 = GSM 7-bit, 01 = 8-bit, 10 = UCS2 (UTF-16BE), 11 = reserved
	alphabet := (dcs >> 2) & 0x03

	// 6. Service Centre Time Stamp (SCTS) - 7 bytes
	if pos+7 > len(data) {
		return nil, fmt.Errorf("PDU too short for SCTS")
	}
	msg.Timestamp = decodeSCTS(data[pos : pos+7])
	pos += 7

	// 7. User Data Length (UDL)
	if pos >= len(data) {
		return nil, fmt.Errorf("PDU too short for UDL")
	}
	udl := int(data[pos])
	pos++

	// 8. User Data (UD)
	userData := data[pos:]

	// Parse UDH if present
	udhLen := 0
	if hasUDH && len(userData) > 0 {
		udhLen = int(userData[0]) + 1 // +1 for the length byte itself
		if udhLen > len(userData) {
			return nil, fmt.Errorf("UDH length exceeds user data")
		}

		// Parse UDH for multipart info
		msg.parseUDH(userData[1:udhLen])
		userData = userData[udhLen:]

		// Adjust UDL for 7-bit encoding (UDH is counted in septets)
		if alphabet == 0 {
			// Calculate how many septets the UDH takes
			udhSeptets := (udhLen*8 + 6) / 7
			udl -= udhSeptets
		}
	}

	// Decode message text based on alphabet
	switch alphabet {
	case 0: // GSM 7-bit
		// For 7-bit, udl is number of septets
		fillBits := 0
		if hasUDH {
			// Calculate fill bits after UDH
			fillBits = (7 - ((udhLen * 8) % 7)) % 7
		}
		msg.Text = decodeGSM7Bit(userData, udl, fillBits)
	case 1: // 8-bit
		msg.Text = string(userData)
	case 2: // UCS2 (UTF-16BE)
		msg.Text = decodeUCS2(userData)
	default:
		msg.Text = fmt.Sprintf("[Unknown encoding DCS=%02X]", dcs)
	}

	return msg, nil
}

// parseUDH parses User Data Header for multipart info
func (m *PDUMessage) parseUDH(udh []byte) {
	pos := 0
	for pos < len(udh) {
		if pos+1 >= len(udh) {
			break
		}
		iei := udh[pos]   // Information Element Identifier
		iel := udh[pos+1] // Information Element Length
		pos += 2

		if pos+int(iel) > len(udh) {
			break
		}

		ieData := udh[pos : pos+int(iel)]
		pos += int(iel)

		// IEI 0x00 = Concatenated SMS, 8-bit reference
		// IEI 0x08 = Concatenated SMS, 16-bit reference
		if iei == 0x00 && len(ieData) >= 3 {
			m.IsMultipart = true
			m.MultipartRef = int(ieData[0])
			m.TotalParts = int(ieData[1])
			m.PartNumber = int(ieData[2])
		} else if iei == 0x08 && len(ieData) >= 4 {
			m.IsMultipart = true
			m.MultipartRef = int(ieData[0])<<8 | int(ieData[1])
			m.TotalParts = int(ieData[2])
			m.PartNumber = int(ieData[3])
		}
	}
}

// decodePhoneNumber decodes a phone number from swapped nibbles format
func decodePhoneNumber(data []byte, international bool) string {
	result := ""
	if international {
		result = "+"
	}

	for _, b := range data {
		lo := b & 0x0F
		hi := (b >> 4) & 0x0F

		if lo <= 9 {
			result += string('0' + lo)
		}
		if hi <= 9 {
			result += string('0' + hi)
		}
	}

	return result
}

// decodeSCTS decodes Service Centre Time Stamp
func decodeSCTS(data []byte) time.Time {
	if len(data) < 7 {
		return time.Time{}
	}

	// Each byte is BCD with swapped nibbles
	decodeBCD := func(b byte) int {
		return int(b&0x0F)*10 + int((b>>4)&0x0F)
	}

	year := 2000 + decodeBCD(data[0])
	month := decodeBCD(data[1])
	day := decodeBCD(data[2])
	hour := decodeBCD(data[3])
	minute := decodeBCD(data[4])
	second := decodeBCD(data[5])

	// Timezone (in quarter hours, signed)
	tz := data[6]
	tzSign := 1
	if tz&0x08 != 0 { // Bit 3 of low nibble is sign
		tzSign = -1
		tz = tz & 0xF7 // Clear sign bit
	}
	tzQuarters := decodeBCD(tz)
	tzOffset := tzSign * tzQuarters * 15 * 60 // Convert to seconds

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
	if len(data) == 0 {
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

		if byteIdx >= len(data) {
			break
		}

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

// MultipartCollector collects parts of multipart SMS messages
type MultipartCollector struct {
	parts map[string]map[int]*PDUMessage // key: "sender:ref", value: map of part# -> message
}

// NewMultipartCollector creates a new collector
func NewMultipartCollector() *MultipartCollector {
	return &MultipartCollector{
		parts: make(map[string]map[int]*PDUMessage),
	}
}

// Add adds a message part and returns the complete message if all parts received
func (c *MultipartCollector) Add(msg *PDUMessage) *PDUMessage {
	if !msg.IsMultipart {
		return msg
	}

	key := fmt.Sprintf("%s:%d", msg.Sender, msg.MultipartRef)

	if c.parts[key] == nil {
		c.parts[key] = make(map[int]*PDUMessage)
	}

	c.parts[key][msg.PartNumber] = msg

	// Check if we have all parts
	if len(c.parts[key]) == msg.TotalParts {
		// Assemble complete message
		var fullText string
		firstPart := c.parts[key][1]

		for i := 1; i <= msg.TotalParts; i++ {
			if part, ok := c.parts[key][i]; ok {
				fullText += part.Text
			}
		}

		// Clean up
		delete(c.parts, key)

		// Return assembled message
		return &PDUMessage{
			Sender:    firstPart.Sender,
			Timestamp: firstPart.Timestamp,
			Text:      fullText,
			SMSC:      firstPart.SMSC,
		}
	}

	return nil // Not complete yet
}

// Pending returns number of incomplete multipart messages
func (c *MultipartCollector) Pending() int {
	return len(c.parts)
}
