#include <WiFi.h>

// ====================================================================================
// CONFIGURE YOUR WI-FI CREDENTIALS HERE
// ====================================================================================
const char* TARGET_SSID = "NidhishiPhone";    // Try "NidhishiPhone" or "NidhishJioAir"
const char* TARGET_PASS = "YOUR_PASSWORD_HERE"; 

void setup() {
  Serial.begin(115200);
  delay(2000);

  Serial.println("\n==========================================");
  Serial.println("    ESP32-C3 Smart BSSID Wi-Fi Connect    ");
  Serial.println("==========================================");

  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);
  delay(500);

  Serial.printf("Step 1: Scanning for \"%s\"...\n", TARGET_SSID);
  int n = WiFi.scanNetworks(false, true);
  int targetIndex = -1;

  for (int i = 0; i < n; i++) {
    if (WiFi.SSID(i) == TARGET_SSID) {
      targetIndex = i;
      break;
    }
  }

  if (targetIndex == -1) {
    Serial.printf("❌ Could not find \"%s\" in scan!\n", TARGET_SSID);
    Serial.println("Nearby networks found:");
    for (int i = 0; i < n; i++) {
      Serial.printf("  [%d] \"%s\" (Ch: %d, RSSI: %d dBm)\n", 
                    i+1, WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i));
    }
    WiFi.scanDelete();
    return;
  }

  int channel = WiFi.channel(targetIndex);
  uint8_t bssid[6];
  memcpy(bssid, WiFi.BSSID(targetIndex), 6);
  int rssi = WiFi.RSSI(targetIndex);

  Serial.printf("✔ Found \"%s\"! Channel: %d, RSSI: %d dBm\n", TARGET_SSID, channel, rssi);
  Serial.printf("  BSSID: %02X:%02X:%02X:%02X:%02X:%02X\n", 
                bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5]);

  WiFi.scanDelete();
  delay(200);

  Serial.println("Step 2: Connecting directly via BSSID & Channel...");
  // Direct connect bypassing channel search:
  WiFi.begin(TARGET_SSID, TARGET_PASS, channel, bssid);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n🎉 [SUCCESS] Wi-Fi Connected!");
    Serial.print("   IP Address:  ");
    Serial.println(WiFi.localIP());
    Serial.print("   Subnet Mask: ");
    Serial.println(WiFi.subnetMask());
    Serial.print("   Gateway IP:  ");
    Serial.println(WiFi.gatewayIP());
    Serial.print("   DNS IP:      ");
    Serial.println(WiFi.dnsIP());
  } else {
    Serial.println("\n❌ [FAILED] Connection failed!");
    Serial.printf("   WiFi.status(): %d\n", WiFi.status());
    if (WiFi.status() == 4) {
      Serial.println("   --> Code 4 = WRONG PASSWORD! Double check the password.");
    } else if (WiFi.status() == 6) {
      Serial.println("   --> Code 6 = DISCONNECTED / AUTH REJECTED (Check WPA security or Hotspot lock).");
    }
  }
}

void loop() {
  if (WiFi.status() == WL_CONNECTED) {
    delay(5000);
    Serial.printf("[Connected] IP: %s | Signal: %d dBm\n", 
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
  } else {
    delay(5000);
    Serial.printf("[Disconnected] Status code: %d\n", WiFi.status());
  }
}

