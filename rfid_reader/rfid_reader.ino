/*
 * Innovations ID-12 card-UID reporter for a dedicated Arduino.
 *
 * Reads 125 kHz EM4100-type tags on an ID-12/ID-12LA module and prints one
 * line per tag placement over USB serial, so the host can use the UID as a
 * scene trigger:
 *
 *     UID:4500A2B3C4
 *
 * This board does NOT run Firmata. It is separate from the board that drives
 * the LEDs/servos. The host reads this board on its own serial port.
 *
 * The ID-12 has an internal antenna and speaks plain TTL serial (9600 baud,
 * ASCII format), so no library is needed. Note it reads ONLY 125 kHz tags;
 * 13.56 MHz MIFARE cards are not detected.
 *
 * Wiring (ID-12 -> Arduino Uno). The module has 2 mm pin pitch: use a
 * breakout board or solder wires directly.
 *     Pin 1  GND -> GND
 *     Pin 2  RES -> 5V (held high)
 *     Pin 7  FS  -> GND (selects ASCII output, 9600 baud)
 *     Pin 9  D0  -> D8  (TTL serial data)
 *     Pin 11 VCC -> 5V (ID-12LA also works at 3.3V)
 *
 * ASCII frame from the module:
 *     STX(0x02)  10 hex chars (5 data bytes)  2 hex chars (XOR checksum)
 *     CR LF ETX(0x03)
 */

#include <SoftwareSerial.h>

#define RFID_RX_PIN 8
#define RFID_TX_PIN 9  // unused; the ID-12 is output-only

SoftwareSerial rfid(RFID_RX_PIN, RFID_TX_PIN);

// Converts one ASCII hex digit to its value, or -1 if not a hex digit.
int hexValue(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;
}

void setup() {
  Serial.begin(115200);
  rfid.begin(9600);
}

void loop() {
  if (!rfid.available() || rfid.read() != 0x02) {
    return;
  }

  // Collect the 12 hex chars (10 UID + 2 checksum) that follow STX, skipping
  // CR/LF and bailing out on timeout so a garbled frame cannot wedge the loop.
  char hex[12];
  byte count = 0;
  unsigned long start = millis();
  while (count < 12 && millis() - start < 100) {
    if (!rfid.available()) {
      continue;
    }
    char c = rfid.read();
    if (c == 0x03) {  // ETX before 12 digits: truncated frame
      return;
    }
    if (hexValue(c) >= 0) {
      hex[count++] = c;
    }
  }
  if (count < 12) {
    return;
  }

  // Checksum: XOR of the 5 data bytes must equal the 6th byte.
  byte bytes[6];
  for (byte i = 0; i < 6; i++) {
    bytes[i] = (hexValue(hex[2 * i]) << 4) | hexValue(hex[2 * i + 1]);
  }
  byte checksum = bytes[0] ^ bytes[1] ^ bytes[2] ^ bytes[3] ^ bytes[4];
  if (checksum != bytes[5]) {
    return;
  }

  Serial.print("UID:");
  for (byte i = 0; i < 10; i++) {
    Serial.print((char)toupper(hex[i]));
  }
  Serial.println();
}
