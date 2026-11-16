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
 ======================================================================================
*/

#include <Wire.h>
#include <WiFi.h>
#include <PubSubClient.h>

// ====================================================================================
// 1. WI-FI & MQTT SETTINGS (Update these!)
// ====================================================================================
const char *WIFI_SSID     = "YOUR_WIFI_NAME";        // <--- Your 2.4 GHz Wi-Fi Name
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";    // <--- Your Wi-Fi Password

// MQTT Broker (Use your Raspberry Pi's IP, or public broker "broker.hivemq.com")
const char *MQTT_BROKER   = "192.168.31.231";        // <--- IP of Raspberry Pi (or broker.hivemq.com)
const int   MQTT_PORT     = 1883;
const char *MQTT_TOPIC    = "baymax/fall";

WiFiClient espClient;
PubSubClient mqttClient(espClient);

// ====================================================================================
// 2. HARDWARE PINS & THRESHOLDS
// ====================================================================================
#define SDA_PIN 8
#define SCL_PIN 9
#define MPU_ADDR 0x68

const float FREEFALL_G           = 0.45;     // Weightlessness (< 0.45g)
const unsigned long FREEFALL_MIN_MS = 40;    // Min freefall duration
const float IMPACT_G             = 2.50;     // Impact shock (> 2.5g)
const unsigned long IMPACT_WINDOW_MS = 1000; // Window between freefall & impact
const unsigned long SETTLE_MS    = 500;      // Settle time
const float STILL_TOL_G          = 0.25;     // Rest acceleration tolerance
const float STILL_GYRO_DPS       = 40.0;     // Rest gyro tolerance
const unsigned long INACTIVE_TIME_MS = 2000; // Immobility duration (2s)
const unsigned long COOLDOWN_MS  = 5000;     // Cooldown between alerts

const float ACCEL_LSB_PER_G      = 4096.0;   // +-8 g scale
const float GYRO_LSB_PER_DPS     = 65.5;     // +-500 dps scale

enum State { IDLE, FREEFALL, WAIT_IMPACT_DONE, CHECK_INACTIVITY };
State state = IDLE;

unsigned long stateStart = 0;
unsigned long stillSince = 0;
unsigned long lastAlert  = 0;
unsigned long lastSample = 0;
unsigned long lastMqttRetry = 0;

// ====================================================================================
// 3. WI-FI & MQTT CONNECTION
// ====================================================================================
void setupWiFi() {
  Serial.printf("\nConnecting to Wi-Fi \"%s\"...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(400);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("✔ Wi-Fi Connected!");
    Serial.print("   ESP32 IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("❌ Wi-Fi Connection Failed. Check SSID/Password and 2.4 GHz band.");
  }
}

void connectMQTT() {
  if (WiFi.status() != WL_CONNECTED) return;

  if (!mqttClient.connected()) {
    unsigned long now = millis();
    if (now - lastMqttRetry > 3000) {
      lastMqttRetry = now;
      Serial.printf("Connecting to MQTT broker at %s:%d... ", MQTT_BROKER, MQTT_PORT);
      String clientId = "ESP32_Fall_" + String(random(0xffff), HEX);
      if (mqttClient.connect(clientId.c_str())) {
        Serial.println("✔ Connected to MQTT Broker!");
      } else {
        Serial.printf("Failed (rc=%d), retrying in 3s\n", mqttClient.state());
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

bool mpuRead(float &ax, float &ay, float &az, float &gx, float &gy, float &gz) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)14) != 14) return false;

  int16_t rax = (Wire.read() << 8) | Wire.read();
  int16_t ray = (Wire.read() << 8) | Wire.read();
  int16_t raz = (Wire.read() << 8) | Wire.read();
  Wire.read(); Wire.read();
  int16_t rgx = (Wire.read() << 8) | Wire.read();
  int16_t rgy = (Wire.read() << 8) | Wire.read();
  int16_t rgz = (Wire.read() << 8) | Wire.read();

  ax = rax / ACCEL_LSB_PER_G;  ay = ray / ACCEL_LSB_PER_G;  az = raz / ACCEL_LSB_PER_G;
  gx = rgx / GYRO_LSB_PER_DPS; gy = rgy / GYRO_LSB_PER_DPS; gz = rgz / GYRO_LSB_PER_DPS;
  return true;
}

// ====================================================================================
// 5. SETUP
// ====================================================================================
void setup() {
  Serial.begin(115200);
  delay(1000);

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
    Serial.printf("❌ IMU not detected (0x%02X). Check SDA (GPIO 8) and SCL (GPIO 9)!\n", who);
    while (true) delay(1000);
  }
  Serial.printf("✔ MPU-6050 detected (ID: 0x%02X)\n", who);

  // Configure MPU-6050
  mpuWrite(0x6B, 0x00); // Wake up
  mpuWrite(0x1A, 0x03); // DLPF ~44 Hz
  mpuWrite(0x1B, 0x08); // Gyro +-500 dps
  mpuWrite(0x1C, 0x10); // Accel +-8 g

  setupWiFi();

  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);

  Serial.println("✔ Fall detection active. Waiting for fall events...\n");
}

// ====================================================================================
// 6. MAIN LOOP
// ====================================================================================
void loop() {
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
        Serial.printf("💥 Impact detected: %.2f g\n", accMag);
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
            Serial.println("\n🚨 >>> FALL DETECTED! Sending to Raspberry Pi via MQTT... <<<");
            lastAlert = now;

            if (mqttClient.connected()) {
              mqttClient.publish(MQTT_TOPIC, "FALL_DETECTED");
              Serial.printf("📡 Published 'FALL_DETECTED' to topic: %s\n\n", MQTT_TOPIC);
            } else {
              Serial.println("⚠ MQTT Not connected, could not send fall packet.");
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
