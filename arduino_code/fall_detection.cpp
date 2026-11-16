/*
 ======================================================================================
  BaymaxCare - ESP32-C3 + MPU6050 Fall Detection with MQTT & Web Portal
  Board: ESP32-C3 (e.g. ESP32-C3 SuperMini, DevKitM-1)
  Sensor: MPU-6050 Accelerometer & Gyroscope (I2C)

  Wiring:
    ESP32-C3 GPIO 8  -> MPU6050 SDA
    ESP32-C3 GPIO 9  -> MPU6050 SCL
    ESP32-C3 3.3V/5V -> MPU6050 VCC
    ESP32-C3 GND     -> MPU6050 GND

  Required Libraries (Install via Arduino Library Manager):
    1. "PubSubClient" by Nick O'Leary
 ======================================================================================
*/

#include <Wire.h>
#include <WiFi.h>
#include <WebServer.h>
#include <PubSubClient.h>
#include <HTTPClient.h>

// ====================================================================================
// 1. NETWORK & MQTT CONFIGURATION (Change these to match your setup)
// ====================================================================================
const char *WIFI_SSID     = "YOUR_WIFI_NAME";      // <--- Put your Wi-Fi name here
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";  // <--- Put your Wi-Fi password here

// Fallback SoftAP mode (if home Wi-Fi is unreachable)
const char *AP_SSID       = "Baymax_FallDetector";
const char *AP_PASSWORD   = "12345678";

// Central Server & MQTT Broker Configuration
const char *SERVER_IP       = "192.168.31.172";    // <--- IP of PC running server.py
const int   SERVER_PORT     = 8000;
const char *MQTT_BROKER     = "broker.hivemq.com"; // Free public broker (or use SERVER_IP if Mosquitto is running)
const int   MQTT_PORT       = 1883;
const char *MQTT_TOPIC_FALL = "baymax/fall_alert";
const char *PATIENT_CODE    = "P-1042";

// ====================================================================================
// 2. HARDWARE PINS & I2C SETUP (ESP32-C3 defaults: SDA=8, SCL=9)
// ====================================================================================
#define SDA_PIN 8
#define SCL_PIN 9
#define MPU_ADDR 0x68

// ====================================================================================
// 3. FALL DETECTION TUNABLE THRESHOLDS
// ====================================================================================
const float FREEFALL_G           = 0.45;     // Weightlessness threshold (< 0.45g)
const unsigned long FREEFALL_MIN_MS = 40;    // Minimum freefall duration (ms)
const float IMPACT_G             = 2.50;     // Impact shock threshold (> 2.5g)
const unsigned long IMPACT_WINDOW_MS = 1000; // Max time between freefall and impact
const unsigned long SETTLE_MS    = 500;      // Settle time after impact
const float STILL_TOL_G          = 0.25;     // Rest acceleration tolerance (|a - 1g| < 0.25)
const float STILL_GYRO_DPS       = 40.0;     // Rest gyro tolerance (< 40 deg/s)
const unsigned long INACTIVE_TIME_MS = 2000; // Required stillness duration (2 seconds)
const unsigned long COOLDOWN_MS  = 5000;     // Prevent duplicate triggers within 5s

const float ACCEL_LSB_PER_G      = 4096.0;   // +-8 g scale
const float GYRO_LSB_PER_DPS     = 65.5;     // +-500 dps scale

// State Machine
enum State { IDLE, FREEFALL, WAIT_IMPACT_DONE, CHECK_INACTIVITY };
State state = IDLE;

unsigned long stateStart = 0;
unsigned long stillSince = 0;
unsigned long lastAlert  = 0;
unsigned long lastSample = 0;
unsigned long lastMqttRetry = 0;

// Shared status for Web server
volatile bool fallActive = false;
unsigned long fallTimeMs = 0;
int fallCount = 0;
float latestAcc = 1.0;

WiFiClient espClient;
PubSubClient mqttClient(espClient);
WebServer server(80);

// ====================================================================================
// 4. EMBEDDED DIAGNOSTIC WEB PAGE
// ====================================================================================
const char PAGE[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BaymaxCare - Fall Sensor Node</title>
<style>
  body { margin:0; font-family: -apple-system, BlinkMacSystemFont, Arial, sans-serif; text-align:center; color:#fff;
         background:#1b5e20; min-height:100vh; display:flex; flex-direction:column;
         justify-content:center; align-items:center; transition: background .3s; }
  body.fall { background:#b71c1c; animation: flash 0.8s infinite; }
  body.off  { background:#424242; }
  @keyframes flash { 50% { background:#e53935; } }
  h1 { font-size: 2.4em; margin: 0 0 10px; }
  p  { font-size: 1.1em; margin: 6px; }
  .badge { background:rgba(255,255,255,0.2); padding:4px 12px; border-radius:12px; font-weight:bold; }
  button { margin-top:20px; padding:12px 28px; font-size:1.1em; border:0; font-weight:bold;
           border-radius:8px; background:#fff; color:#b71c1c; cursor:pointer; display:none; }
  body.fall button { display:inline-block; }
</style>
</head>
<body>
  <h1 id="title">Normal</h1>
  <p><span class="badge" id="mqttStatus">Connecting to MQTT...</span></p>
  <p id="info">Reading MPU6050 sensor...</p>
  <p id="acc"></p>
  <button onclick="ack()">Acknowledge Alert</button>
<script>
  async function update() {
    try {
      const r = await fetch('/status');
      const s = await r.json();
      document.body.className = s.fall ? 'fall' : '';
      document.getElementById('title').textContent = s.fall ? '⚠ FALL DETECTED!' : 'Normal';
      document.getElementById('info').textContent = s.fall
        ? 'Impact detected ' + s.since + ' s ago (Baymax companion checking...)'
        : 'Active monitoring (Total falls detected: ' + s.count + ')';
      document.getElementById('acc').textContent = 'Current Acceleration: ' + s.acc.toFixed(2) + ' g';
      document.getElementById('mqttStatus').textContent = s.mqtt ? 'MQTT Connected' : 'MQTT Disconnected';
      document.getElementById('mqttStatus').style.background = s.mqtt ? 'rgba(76,175,80,0.4)' : 'rgba(244,67,54,0.4)';
    } catch (e) {
      document.body.className = 'off';
      document.getElementById('title').textContent = 'Disconnected';
      document.getElementById('info').textContent = 'Cannot reach ESP32';
      document.getElementById('acc').textContent = '';
    }
  }
  async function ack() { await fetch('/ack'); update(); }
  setInterval(update, 500);
  update();
</script>
</body>
</html>
)rawliteral";

void handleRoot() { server.send_P(200, "text/html", PAGE); }

void handleStatus() {
  char json[160];
  unsigned long since = fallActive ? (millis() - fallTimeMs) / 1000 : 0;
  snprintf(json, sizeof(json),
           "{\"fall\":%s,\"since\":%lu,\"count\":%d,\"acc\":%.2f,\"mqtt\":%s}",
           fallActive ? "true" : "false", since, fallCount, latestAcc,
           mqttClient.connected() ? "true" : "false");
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json", json);
}

void handleAck() {
  fallActive = false;
  Serial.println("Alert manually acknowledged from ESP32 web page.");
  server.send(200, "text/plain", "ok");
}

// ====================================================================================
// 5. WI-FI & MQTT CLIENT HELPERS
// ====================================================================================
void setupWiFi() {
  bool connected = false;

  if (strlen(WIFI_SSID) > 0 && strcmp(WIFI_SSID, "YOUR_WIFI_NAME") != 0) {
    Serial.printf("\nConnecting to Wi-Fi: \"%s\"...\n", WIFI_SSID);
    WiFi.disconnect(true, true);
    delay(500);
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 25000) {
      delay(400);
      Serial.print(".");
    }
    Serial.println();
    connected = (WiFi.status() == WL_CONNECTED);

    if (!connected) {
      Serial.println("\n❌ Wi-Fi Connection Failed!");
      Serial.printf("   Status Code: %d (", WiFi.status());
      switch (WiFi.status()) {
        case WL_NO_SSID_AVAIL:  Serial.print("SSID not found - make sure 2.4 GHz Wi-Fi band is active"); break;
        case WL_CONNECT_FAILED: Serial.print("Password incorrect or WPA auth rejected"); break;
        case WL_DISCONNECTED:   Serial.print("Handshake failed or router rejected ESP32"); break;
        default:                Serial.print("DHCP timeout"); break;
      }
      Serial.println(")");

      // Scan and list visible 2.4 GHz networks
      Serial.println("\n--- Nearby 2.4 GHz Wi-Fi Networks Detected by ESP32 ---");
      int n = WiFi.scanNetworks();
      if (n == 0) {
        Serial.println("  No networks detected. Please move ESP32 closer to the router.");
      } else {
        for (int i = 0; i < n && i < 8; ++i) {
          Serial.printf("  [%d] \"%s\" (Signal: %d dBm)\n", i + 1, WiFi.SSID(i).c_str(), WiFi.RSSI(i));
        }
      }
      Serial.println("------------------------------------------------------\n");
    }
  }

  if (connected) {
    Serial.println("✔ Wi-Fi connected successfully!");
    Serial.print("✔ ESP32 IP address: http://");
    Serial.println(WiFi.localIP());
  } else {
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_SSID, AP_PASSWORD);
    Serial.printf("⚠ Access Point active. Connect phone/PC to \"%s\" (password: %s)\n", AP_SSID, AP_PASSWORD);
    Serial.print("Open in browser: http://");
    Serial.println(WiFi.softAPIP());
  }
}

void maintainMqtt() {
  if (WiFi.status() != WL_CONNECTED) return;

  if (!mqttClient.connected()) {
    unsigned long now = millis();
    if (now - lastMqttRetry > 3000) {
      lastMqttRetry = now;
      Serial.printf("Connecting to MQTT broker at %s:%d... ", MQTT_BROKER, MQTT_PORT);
      String clientId = "Baymax_ESP32_";
      clientId += String(random(0xffff), HEX);
      if (mqttClient.connect(clientId.c_str())) {
        Serial.println("Connected to MQTT!");
      } else {
        Serial.printf("Failed (rc=%d), will retry in 3s\n", mqttClient.state());
      }
    }
  } else {
    mqttClient.loop();
  }
}

// ====================================================================================
// 6. MPU-6050 I2C DRIVER
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
  Wire.read(); Wire.read(); // Skip temp
  int16_t rgx = (Wire.read() << 8) | Wire.read();
  int16_t rgy = (Wire.read() << 8) | Wire.read();
  int16_t rgz = (Wire.read() << 8) | Wire.read();

  ax = rax / ACCEL_LSB_PER_G;  ay = ray / ACCEL_LSB_PER_G;  az = raz / ACCEL_LSB_PER_G;
  gx = rgx / GYRO_LSB_PER_DPS; gy = rgy / GYRO_LSB_PER_DPS; gz = rgz / GYRO_LSB_PER_DPS;
  return true;
}

// ====================================================================================
// 7. ARDUINO SETUP
// ====================================================================================
void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) delay(10);

  Serial.println("\n===========================================");
  Serial.println("   BaymaxCare ESP32-C3 Fall Detector Node   ");
  Serial.println("===========================================");

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);

  // Probe MPU-6050 WHO_AM_I register
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x75);
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)1);
  uint8_t who = Wire.available() ? Wire.read() : 0;
  if (who == 0x00 || who == 0xFF) {
    Serial.printf("❌ IMU not detected (WHO_AM_I = 0x%02X). Check SDA/SCL wiring!\n", who);
    while (true) delay(1000);
  }
  Serial.printf("✔ MPU-6050 detected (ID: 0x%02X)\n", who);

  // Initialize MPU-6050 registers
  mpuWrite(0x6B, 0x00);   // Wake up MPU
  mpuWrite(0x1A, 0x03);   // Digital Low Pass Filter ~44 Hz
  mpuWrite(0x1B, 0x08);   // Gyro +-500 deg/s
  mpuWrite(0x1C, 0x10);   // Accel +-8 g

  setupWiFi();

  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);

  server.on("/", handleRoot);
  server.on("/status", handleStatus);
  server.on("/ack", handleAck);
  server.begin();

  Serial.println("✔ Fall detection state machine active. Monitoring...");
}

// ====================================================================================
// 8. MAIN LOOP
// ====================================================================================
void loop() {
  server.handleClient();
  maintainMqtt();

  unsigned long now = millis();
  if (now - lastSample < 10) return;   // ~100 Hz sampling rate
  lastSample = now;

  float ax, ay, az, gx, gy, gz;
  if (!mpuRead(ax, ay, az, gx, gy, gz)) return;

  float accMag  = sqrt(ax * ax + ay * ay + az * az);
  float gyroMag = sqrt(gx * gx + gy * gy + gz * gz);
  latestAcc = accMag;

  switch (state) {

    // 1. Normal state: watch for sudden drop (freefall)
    case IDLE:
      if (accMag < FREEFALL_G) {
        state = FREEFALL;
        stateStart = now;
      }
      break;

    // 2. Freefall detected: watch for impact spike
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

    // 3. Impact occurred: let vibrations settle briefly
    case WAIT_IMPACT_DONE:
      if (now - stateStart >= SETTLE_MS) {
        state = CHECK_INACTIVITY;
        stateStart = now;
        stillSince = 0;
      }
      break;

    // 4. Check for prolonged post-fall immobility (2 seconds of stillness)
    case CHECK_INACTIVITY: {
      bool still = (fabs(accMag - 1.0) < STILL_TOL_G) && (gyroMag < STILL_GYRO_DPS);

      if (still) {
        if (stillSince == 0) stillSince = now;
        if (now - stillSince >= INACTIVE_TIME_MS) {
          if (now - lastAlert > COOLDOWN_MS || lastAlert == 0) {
            Serial.println("\n🚨 >>> FALL DETECTED BY SENSOR! <<<");
            fallActive = true;
            fallTimeMs = now;
            fallCount++;
            lastAlert = now;

            // 1. Publish alert to MQTT
            if (!mqttClient.connected()) {
              maintainMqtt();
            }
            if (mqttClient.connected()) {
              char payload[160];
              snprintf(payload, sizeof(payload),
                       "{\"event\":\"fall_detected\",\"patient_code\":\"%s\",\"acc\":%.2f,\"count\":%d,\"time\":%lu}",
                       PATIENT_CODE, latestAcc, fallCount, now);
              mqttClient.publish(MQTT_TOPIC_FALL, payload);
              Serial.printf("📡 Published to MQTT [%s]: %s\n", MQTT_TOPIC_FALL, payload);
            } else {
              Serial.println("⚠ MQTT broker not connected; falling back to HTTP...");
            }

            // 2. Direct HTTP Post to FastAPI Server (Guarantees delivery to server and Raspberry Pi)
            if (WiFi.status() == WL_CONNECTED) {
              HTTPClient http;
              char url[128];
              snprintf(url, sizeof(url), "http://%s:%d/api/fall/trigger", SERVER_IP, SERVER_PORT);
              http.begin(url);
              http.addHeader("Content-Type", "application/json");
              char httpPayload[128];
              snprintf(httpPayload, sizeof(httpPayload),
                       "{\"patient_code\":\"%s\",\"stage\":\"verifying\",\"acc\":%.2f}",
                       PATIENT_CODE, latestAcc);
              int code = http.POST(httpPayload);
              if (code > 0) {
                Serial.printf("✔ Fall alert successfully delivered to Server via HTTP (Code %d)\n\n", code);
              } else {
                Serial.printf("⚠ Server HTTP post failed: %s\n\n", http.errorToString(code).c_str());
              }
              http.end();
            }
          }
          state = IDLE;
        }
      } else {
        stillSince = 0;
      }

      // If user moved within 6 seconds of impact, cancel (not an incapacitating fall)
      if (now - stateStart > 6000) {
        Serial.println("Movement detected after impact -> Normal recovery, no emergency.");
        state = IDLE;
      }
      break;
    }
  }
}
