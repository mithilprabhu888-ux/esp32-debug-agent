/**
 * ESP32 Debug Agent — demo firmware
 *
 * A small "device" firmware with a serial command console, designed to be
 * driven by an AI agent through the MCP server (server/main.py).
 *
 * Commands (sent over the virtual serial port):
 *   HELP          - list commands
 *   LED ON / OFF  - toggle the status LED (GPIO 26)
 *   STATUS        - device state: uptime, led, free heap
 *   READ          - read DHT22 (temp/hum) and potentiometer (ADC)
 *
 * The board also streams a [DATA] reading every 5 seconds.
 */

#include <Arduino.h>
#include <DHT.h>

#define DHT_PIN 4    // DHT22 data pin
#define LED_PIN 26   // status LED pin
#define POT_PIN 34   // potentiometer ADC pin (input only pin)

DHT dht(DHT_PIN, DHT22);

static unsigned long lastReport = 0;
static bool ledState = false;

static void readDht() {
  float h = dht.readHumidity();
  float t = dht.readTemperature();
  if (isnan(h) || isnan(t)) {
    Serial.println("[DATA] temp=NaN hum=NaN   (sensor read failed)");
  } else {
    Serial.printf("[DATA] temp=%.1fC hum=%.1f%%\n", t, h);
  }
}

static void readPot() {
  int raw = analogRead(POT_PIN);
  Serial.printf("[DATA] pot=%d/4095\n", raw);
}

static void printHelp() {
  Serial.println("[HELP] commands: HELP | LED ON | LED OFF | STATUS | READ");
}

static void handleCommand(const String& cmd) {
  if (cmd == "HELP") {
    printHelp();
  } else if (cmd == "LED ON") {
    digitalWrite(LED_PIN, HIGH);
    ledState = true;
    Serial.println("[OK] LED=ON");
  } else if (cmd == "LED OFF") {
    digitalWrite(LED_PIN, LOW);
    ledState = false;
    Serial.println("[OK] LED=OFF");
  } else if (cmd == "STATUS") {
    Serial.printf("[STATUS] state=RUNNING uptime=%lus led=%d freeHeap=%u\n",
                  millis() / 1000, ledState ? 1 : 0,
                  (unsigned)ESP.getFreeHeap());
  } else if (cmd == "READ") {
    readDht();
    readPot();
  } else if (cmd.length() > 0) {
    Serial.printf("[ERR] unknown command: %s\n", cmd.c_str());
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(POT_PIN, INPUT);
  dht.begin();
  Serial.println();
  Serial.println("[BOOT] ESP32 Debug Agent demo firmware v1.0");
  Serial.println("[BOOT] Type HELP for available commands.");
}

void loop() {
  // Handle serial commands
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toUpperCase();
    handleCommand(cmd);
  }

  // Periodic sensor report every 5 s of device time
  if (millis() - lastReport >= 5000) {
    lastReport = millis();
    readDht();
    readPot();
  }

  // FAULT-INJECTION-POINT (blocking_delay inserts code after this line)
}
