/*
 ======================================================================================
  BaymaxCare: ESP32-C3 + MPU6050 Fall Detector -> Raspberry Pi via MQTT
  Board: ESP32-C3
  Wiring:
    ESP32-C3 GPIO 8  -> MPU6050 SDA
    ESP32-C3 GPIO 9  -> MPU6050 SCL
    ESP32-C3 3.3V/5V -> MPU6050 VCC
    ESP32-C3 GND     -> MPU6050 GND

  Required Library: "PubSubClient" by Nick O'Leary

  Changes from the previous version:
    - WiFi.setTxPower(8.5 dBm) before WiFi.begin()  (the fix for your board)
    - Removed the boot-time scan that called WiFi.disconnect(true), which switched
      the radio off right before connecting. Scan is now optional (DO_SCAN_ON_BOOT).
    - WiFi event handler prints the real disconnect reason code.
    - Non-blocking WiFi auto-reconnect in loop().
    - Fixed I2C byte-order bug: (Wire.read() << 8) | Wire.read() has unspecified
      evaluation order in C++, so bytes could be swapped. Now read explicitly.
 ======================================================================================
*/

#include <Wire.h>
#include <WiFi.h>
#include <PubSubClient.h>

// ====================================================================================
// 1. WI-FI & MQTT SETTINGS (Update these!)
// ====================================================================================
const char *WIFI_SSID     = "NidhishiPhone";        // <--- Your Mobile Hotspot SSID
const char *WIFI_PASSWORD = "12345678";             // <--- Your Mobile Hotspot Password

#define DO_SCAN_ON_BOOT 0   // set to 1 to print nearby networks at startup

const char *MQTT_BROKER   = "broker.hivemq.com";
const int   MQTT_PORT     = 1883;
const char *MQTT_TOPIC    = "baymaxcare/P-1042/fall";

WiFiClient espClient;
PubSubClient mqttClient(espClient);

// ====================================================================================
// 2. HARDWARE PINS & THRESHOLDS (High Sensitivity Mode)
// ====================================================================================
#define SDA_PIN 8
#define SCL_PIN 9
#define MPU_ADDR 0x68

// Sensitivity Tuning:
const float FREEFALL_G           = 0.65;     // Weightlessness threshold (< 0.65g, easier to trigger)
const unsigned long FREEFALL_MIN_MS = 25;    // Min freefall duration (25 ms)
const float IMPACT_G             = 1.80;     // Impact threshold (> 1.80g, triggers on softer falls)
const unsigned long IMPACT_WINDOW_MS = 1200; // Window between freefall & impact
const unsigned long SETTLE_MS    = 400;      // Settle time after impact
const float STILL_TOL_G          = 0.40;     // Rest acceleration tolerance (+- 0.40g around 1.0g)
const float STILL_GYRO_DPS       = 60.0;     // Rest gyro tolerance (ignores minor tremors/noise)
const unsigned long INACTIVE_TIME_MS = 1000; // Immobility duration (1s instead of 2s for faster response)
const unsigned long COOLDOWN_MS  = 4000;     // Cooldown between alerts (4s)

const float ACCEL_LSB_PER_G      = 4096.0;   // +-8 g scale
const float GYRO_LSB_PER_DPS     = 65.5;     // +-500 dps scale

enum State { IDLE, FREEFALL, WAIT_IMPACT_DONE, CHECK_INACTIVITY };
State state = IDLE;

unsigned long stateStart = 0;
unsigned long stillSince = 0;
unsigned long lastAlert  = 0;
unsigned long lastSample = 0;
unsigned long lastMqttRetry = 0;
unsigned long lastWifiRetry = 0;

// ====================================================================================
// 3. WI-FI & MQTT CONNECTION
// ====================================================================================
void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  switch (event) {
    case ARDUINO_EVENT_WIFI_STA_CONNECTED:
      Serial.println("WiFi: associated with router");
      break;
    case ARDUINO_EVENT_WIFI_STA_GOT_IP:
      Serial.print("WiFi: got IP ");
      Serial.println(WiFi.localIP());
      break;
    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
      Serial.printf("WiFi: disconnected, reason code %d\n",
                    info.wifi_sta_disconnected.reason);
      break;
    default:
      break;
  }
}

void scanNearbyNetworks() {
  Serial.println("\n--- Scanning nearby networks ---");
  int n = WiFi.scanNetworks();
  if (n <= 0) {
    Serial.printf("No networks found (result: %d)\n", n);
  } else {
    for (int i = 0; i < n; ++i) {
      Serial.printf("  [%d] \"%s\" | %d dBm | ch %d\n",
                    i + 1, WiFi.SSID(i).c_str(), WiFi.RSSI(i), WiFi.channel(i));
    }
  }
  WiFi.scanDelete();
  Serial.println("--------------------------------\n");
}

void startWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setTxPower(WIFI_POWER_8_5dBm);   // KEY FIX: full power fails on many C3 boards
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void setupWiFi() {
  WiFi.persistent(false);
  WiFi.onEvent(onWiFiEvent);
  WiFi.mode(WIFI_STA);

#if DO_SCAN_ON_BOOT
  scanNearbyNetworks();
#endif

  Serial.printf("Connecting to Wi-Fi \"%s\"", WIFI_SSID);
  startWiFi();

  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(400);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Wi-Fi connected. IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("Wi-Fi not connected yet. Will keep retrying in the background.");
  }
}

// Call from loop(): retries WiFi every 10 s without blocking sensor sampling
void maintainWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  unsigned long now = millis();
  if (now - lastWifiRetry > 10000) {
    lastWifiRetry = now;
    Serial.println("Retrying Wi-Fi...");
    WiFi.disconnect();
    startWiFi();
  }
}

void connectMQTT() {
  if (WiFi.status() != WL_CONNECTED) return;

  if (!mqttClient.connected()) {
    unsigned long now = millis();
    if (now - lastMqttRetry > 3000) {
      lastMqttRetry = now;
      Serial.printf("Connecting to MQTT broker %s:%d... ", MQTT_BROKER, MQTT_PORT);
      String clientId = "ESP32_Fall_" + String((uint32_t)esp_random(), HEX);
      if (mqttClient.connect(clientId.c_str())) {
        Serial.println("connected");
      } else {
        Serial.printf("failed (rc=%d), retrying in 3 s\n", mqttClient.state());
      }
    }
  } else {
    mqttClient.loop();
  }
}

// ====================================================================================
// 4. MPU-6050 READ/WRITE
// ====================================================================================
void mpuWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

static inline int16_t readInt16() {
  uint8_t hi = Wire.read();   // explicit order; avoids unspecified evaluation order
  uint8_t lo = Wire.read();
  return (int16_t)((hi << 8) | lo);
}

bool mpuRead(float &ax, float &ay, float &az, float &gx, float &gy, float &gz) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)14) != 14) return false;

  int16_t rax = readInt16();
  int16_t ray = readInt16();
  int16_t raz = readInt16();
  readInt16();                 // temperature, unused
  int16_t rgx = readInt16();
  int16_t rgy = readInt16();
  int16_t rgz = readInt16();

  ax = rax / ACCEL_LSB_PER_G;  ay = ray / ACCEL_LSB_PER_G;  az = raz / ACCEL_LSB_PER_G;
  gx = rgx / GYRO_LSB_PER_DPS; gy = rgy / GYRO_LSB_PER_DPS; gz = rgz / GYRO_LSB_PER_DPS;
  return true;
}

// ====================================================================================
// 5. SETUP
// ====================================================================================
void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0); // Critical: Prevents ESP32-C3 from freezing when not connected to PC USB!
  
  unsigned long tSerial = millis();
  while (!Serial && millis() - tSerial < 2000) delay(10);
  delay(200);

  Serial.println("\n--- ESP32-C3 Fall Detector Initializing ---");

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);

  // Check MPU WHO_AM_I
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x75);
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)1);
  uint8_t who = Wire.available() ? Wire.read() : 0;
  if (who == 0x00 || who == 0xFF) {
    while (true) {   // repeat so the message is visible even if the monitor opens late
      Serial.printf("IMU not detected (0x%02X). Check SDA (GPIO 8), SCL (GPIO 9), VCC, GND!\n", who);
      delay(1000);
    }
  }
  Serial.printf("MPU-6050 detected (ID: 0x%02X)\n", who);

  // Configure MPU-6050
  mpuWrite(0x6B, 0x00); // Wake up
  mpuWrite(0x1A, 0x03); // DLPF ~44 Hz
  mpuWrite(0x1B, 0x08); // Gyro +-500 dps
  mpuWrite(0x1C, 0x10); // Accel +-8 g

  setupWiFi();

  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);

  Serial.println("Fall detection active. Waiting for fall events...\n");
}

// ====================================================================================
// 6. MAIN LOOP
// ====================================================================================
void loop() {
  maintainWiFi();
  connectMQTT();

  unsigned long now = millis();
  if (now - lastSample < 10) return; // 100 Hz sampling
  lastSample = now;

  float ax, ay, az, gx, gy, gz;
  if (!mpuRead(ax, ay, az, gx, gy, gz)) return;

  float accMag  = sqrt(ax * ax + ay * ay + az * az);
  float gyroMag = sqrt(gx * gx + gy * gy + gz * gz);

  switch (state) {

    case IDLE:
      if (accMag < FREEFALL_G) {
        state = FREEFALL;
        stateStart = now;
      }
      break;

    case FREEFALL:
      if (accMag > IMPACT_G && (now - stateStart) >= FREEFALL_MIN_MS) {
        Serial.printf("Impact detected: %.2f g\n", accMag);
        state = WAIT_IMPACT_DONE;
        stateStart = now;
      } else if (accMag >= FREEFALL_G && (now - stateStart) < FREEFALL_MIN_MS) {
        state = IDLE;
      } else if (now - stateStart > IMPACT_WINDOW_MS) {
        state = IDLE;
      }
      break;

    case WAIT_IMPACT_DONE:
      if (now - stateStart >= SETTLE_MS) {
        state = CHECK_INACTIVITY;
        stateStart = now;
        stillSince = 0;
      }
      break;

    case CHECK_INACTIVITY: {
      bool still = (fabs(accMag - 1.0) < STILL_TOL_G) && (gyroMag < STILL_GYRO_DPS);

      if (still) {
        if (stillSince == 0) stillSince = now;
        if (now - stillSince >= INACTIVE_TIME_MS) {
          if (now - lastAlert > COOLDOWN_MS || lastAlert == 0) {
            Serial.println("\n>>> FALL DETECTED! Sending to Raspberry Pi via MQTT... <<<");
            lastAlert = now;

            if (mqttClient.connected()) {
              mqttClient.publish(MQTT_TOPIC, "FALL_DETECTED");
              Serial.printf("Published 'FALL_DETECTED' to topic: %s\n\n", MQTT_TOPIC);
            } else {
              Serial.println("MQTT not connected, could not send fall packet.");
            }
          }
          state = IDLE;
        }
      } else {
        stillSince = 0;
      }

      if (now - stateStart > 6000) {
        Serial.println("Movement detected -> Patient recovered, no alert.");
        state = IDLE;
      }
      break;
    }
  }
}
