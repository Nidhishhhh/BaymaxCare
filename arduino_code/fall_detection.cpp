#include <Wire.h>
#include <WiFi.h>
#include <WebServer.h>
#include <PubSubClient.h>

// ---------------- WiFi settings ----------------
const char *WIFI_SSID     = "";            // your home WiFi name (leave "" for AP mode)
const char *WIFI_PASSWORD = "12345678";    // your home WiFi password
const char *AP_SSID       = "FallDetector";
const char *AP_PASSWORD   = "12345678";    // at least 8 characters

// ---------------- MQTT Broker settings ----------------
const char *MQTT_BROKER     = "192.168.31.172"; // IP of Raspberry Pi or Server PC
const int   MQTT_PORT       = 1883;
const char *MQTT_TOPIC_FALL = "baymax/fall_alert";
const char *PATIENT_CODE    = "P-1042";

WiFiClient espClient;
PubSubClient mqttClient(espClient);
unsigned long lastMqttRetry = 0;

// ---------------- Pins / I2C ----------------
#define SDA_PIN 8
#define SCL_PIN 9
#define MPU_ADDR 0x68

// ---------------- Tunable thresholds ----------------
const float FREEFALL_G        = 0.45;
const unsigned long FREEFALL_MIN_MS = 40;
const float IMPACT_G          = 2.5;
const unsigned long IMPACT_WINDOW_MS = 1000;
const unsigned long SETTLE_MS = 500;
const float STILL_TOL_G       = 0.25;
const float STILL_GYRO_DPS    = 40.0;
const unsigned long INACTIVE_TIME_MS = 2000;
const unsigned long COOLDOWN_MS = 5000;

const float ACCEL_LSB_PER_G   = 4096.0; // +-8 g
const float GYRO_LSB_PER_DPS  = 65.5;   // +-500 dps

enum State { IDLE, FREEFALL, WAIT_IMPACT_DONE, CHECK_INACTIVITY };
State state = IDLE;

unsigned long stateStart = 0, stillSince = 0, lastAlert = 0, lastSample = 0;

// ---------------- Shared status for the web page ----------------
volatile bool fallActive = false;
unsigned long fallTimeMs = 0;
int fallCount = 0;
float latestAcc = 1.0;

WebServer server(80);

// ---------------- Web page ----------------
const char PAGE[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fall Detector</title>
<style>
  body { margin:0; font-family: Arial, sans-serif; text-align:center; color:#fff;
         background:#2e7d32; min-height:100vh; display:flex; flex-direction:column;
         justify-content:center; align-items:center; transition: background .3s; }
  body.fall { background:#c62828; animation: flash 0.8s infinite; }
  body.off  { background:#616161; }
  @keyframes flash { 50% { background:#ff5252; } }
  h1 { font-size: 3em; margin: 0 0 10px; }
  p  { font-size: 1.2em; margin: 6px; }
  button { margin-top:20px; padding:14px 30px; font-size:1.2em; border:0;
           border-radius:8px; background:#fff; color:#c62828; display:none; }
  body.fall button { display:inline-block; }
</style>
</head>
<body>
  <h1 id="title">Normal</h1>
  <p id="info">Connecting...</p>
  <p id="acc"></p>
  <button onclick="ack()">Acknowledge</button>
<script>
  async function update() {
    try {
      const r = await fetch('/status');
      const s = await r.json();
      document.body.className = s.fall ? 'fall' : '';
      document.getElementById('title').textContent = s.fall ? '⚠ FALL DETECTED!' : 'Normal';
      document.getElementById('info').textContent = s.fall
        ? 'Fall detected ' + s.since + ' s ago'
        : 'No fall detected  (total falls: ' + s.count + ')';
      document.getElementById('acc').textContent = 'Acceleration: ' + s.acc.toFixed(2) + ' g';
    } catch (e) {
      document.body.className = 'off';
      document.getElementById('title').textContent = 'Disconnected';
      document.getElementById('info').textContent = 'Cannot reach the device';
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
  char json[128];
  unsigned long since = fallActive ? (millis() - fallTimeMs) / 1000 : 0;
  snprintf(json, sizeof(json),
           "{\"fall\":%s,\"since\":%lu,\"count\":%d,\"acc\":%.2f}",
           fallActive ? "true" : "false", since, fallCount, latestAcc);
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json", json);
}

void handleAck() {
  fallActive = false;
  Serial.println("Alert acknowledged from web page");
  server.send(200, "text/plain", "ok");
}

void setupWiFi() {
  bool connected = false;

  if (strlen(WIFI_SSID) > 0) {
    Serial.printf("Connecting to WiFi \"%s\" ", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
      delay(300);
      Serial.print(".");
    }
    Serial.println();
    connected = (WiFi.status() == WL_CONNECTED);
  }

  if (connected) {
    Serial.print("Open this in your browser: http://");
    Serial.println(WiFi.localIP());
  } else {
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_SSID, AP_PASSWORD);
    Serial.printf("Access point started. Connect to \"%s\" (password %s)\n", AP_SSID, AP_PASSWORD);
    Serial.print("Then open: http://");
    Serial.println(WiFi.softAPIP());
  }
}

// ---------------- IMU helpers ----------------
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

void maintainMqtt() {
  if (WiFi.status() != WL_CONNECTED) return;
  if (!mqttClient.connected()) {
    unsigned long now = millis();
    if (now - lastMqttRetry > 3000) {
      lastMqttRetry = now;
      Serial.printf("Connecting to MQTT broker at %s:%d... ", MQTT_BROKER, MQTT_PORT);
      String clientId = "ESP32_FallDetector_";
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

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) delay(10);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);

  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x75);
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)1);
  uint8_t who = Wire.available() ? Wire.read() : 0;
  if (who == 0x00 || who == 0xFF) {
    Serial.printf("IMU not found (WHO_AM_I = 0x%02X). Check wiring/pins.\n", who);
    while (true) delay(1000);
  }

  mpuWrite(0x6B, 0x00);   // wake up
  mpuWrite(0x1A, 0x03);   // DLPF ~44 Hz
  mpuWrite(0x1B, 0x08);   // gyro  +-500 dps
  mpuWrite(0x1C, 0x10);   // accel +-8 g

  setupWiFi();

  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);

  server.on("/", handleRoot);
  server.on("/status", handleStatus);
  server.on("/ack", handleAck);
  server.begin();

  Serial.println("Fall detection ready. Monitoring...");
}

void loop() {
  server.handleClient();   // always serve the web page
  maintainMqtt();          // keep MQTT connection active

  unsigned long now = millis();
  if (now - lastSample < 10) return;   // ~100 Hz sampling
  lastSample = now;

  float ax, ay, az, gx, gy, gz;
  if (!mpuRead(ax, ay, az, gx, gy, gz)) return;

  float accMag  = sqrt(ax * ax + ay * ay + az * az);
  float gyroMag = sqrt(gx * gx + gy * gy + gz * gz);
  latestAcc = accMag;

  switch (state) {

    case IDLE:
      if (accMag < FREEFALL_G) {
        state = FREEFALL;
        stateStart = now;
      }
      break;

    case FREEFALL:
      if (accMag > IMPACT_G && (now - stateStart) >= FREEFALL_MIN_MS) {
        Serial.printf("Impact detected (%.2f g)\n", accMag);
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
            Serial.println(">>> FALL DETECTED! <<<");
            fallActive = true;
            fallTimeMs = now;
            fallCount++;
            lastAlert = now;

            // Publish to MQTT topic
            if (!mqttClient.connected()) {
              maintainMqtt();
            }
            if (mqttClient.connected()) {
              char payload[160];
              snprintf(payload, sizeof(payload),
                       "{\"event\":\"fall_detected\",\"patient_code\":\"%s\",\"acc\":%.2f,\"count\":%d,\"time\":%lu}",
                       PATIENT_CODE, latestAcc, fallCount, now);
              mqttClient.publish(MQTT_TOPIC_FALL, payload);
              Serial.printf("Published to MQTT [%s]: %s\n", MQTT_TOPIC_FALL, payload);
            } else {
              Serial.println("Warning: MQTT broker not connected; could not publish packet.");
            }
          }
          state = IDLE;
        }
      } else {
        stillSince = 0;
      }

      if (now - stateStart > 6000) {
        Serial.println("Movement after impact - no fall");
        state = IDLE;
      }
      break;
    }
  }
}