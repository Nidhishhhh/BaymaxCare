#include <WiFi.h>

// ====================================================================================
// CONFIGURE YOUR WI-FI CREDENTIALS HERE
// ====================================================================================
const char* ssid     = "NidhishJioAir";      // Try "NidhishJioAir" or "NidhishiPhone"
const char* password = "YOUR_PASSWORD_HERE"; // Put your password here

void setup() {
  Serial.begin(115200);
  delay(2000);

  Serial.println("\n==========================================");
  Serial.println("       ESP32-C3 Minimal Wi-Fi Test        ");
  Serial.println("==========================================");

  // Set WiFi to Station mode and disconnect from any previous session
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);
  delay(500);

  // Print MAC Address
  Serial.print("ESP32 MAC Address: ");
  Serial.println(WiFi.macAddress());

  Serial.printf("Attempting connection to: \"%s\"...\n", ssid);
  WiFi.begin(ssid, password);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.printf(".");
    attempts++;
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n🎉 [SUCCESS] Wi-Fi Connected successfully!");
    Serial.print("   IP Address: ");
    Serial.println(WiFi.localIP());
    Serial.print("   Subnet Mask: ");
    Serial.println(WiFi.subnetMask());
    Serial.print("   Gateway IP:  ");
    Serial.println(WiFi.gatewayIP());
    Serial.print("   Signal Strength (RSSI): ");
    Serial.print(WiFi.RSSI());
    Serial.println(" dBm");
  } else {
    Serial.println("\n❌ [FAILED] Unable to connect.");
    Serial.print("   WiFi.status() code: ");
    Serial.println(WiFi.status());
    Serial.println("   ----------------------------------------");
    Serial.println("   Status code reference:");
    Serial.println("     1 = WL_NO_SSID_AVAIL   (SSID not found/not broadcasting)");
    Serial.println("     4 = WL_CONNECT_FAILED  (Wrong password or authentication rejected)");
    Serial.println("     5 = WL_CONNECTION_LOST (Connection lost during handshake)");
    Serial.println("     6 = WL_DISCONNECTED    (Disconnected / timeout / blocked)");
    Serial.println("   ----------------------------------------");
  }
}

void loop() {
  // If connected, keep printing status every 5 seconds
  if (WiFi.status() == WL_CONNECTED) {
    delay(5000);
    Serial.printf("[WiFi OK] IP: %s | RSSI: %d dBm\n", 
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
  } else {
    delay(5000);
    Serial.printf("[WiFi Disconnected] Status code: %d\n", WiFi.status());
  }
}
