#!/usr/bin/env python3
"""
BaymaxCare In-Home Companion - Fall Verification Service
Subscribes to MQTT topic 'baymax/fall_alert' from ESP32-C3 / MPU6050.
When a fall is detected:
  1. Speaks "Fall detected. Are you okay?" 3 times through the speaker.
  2. If the patient does not respond or cancel, activates emergency protocol:
     - Prints emergency guardian phone call to the console.
     - Publishes 'baymax/fall_confirmed' to turn web dashboards RED.
"""

import sys
import time
import json
import logging
import threading
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("baymaxcare.fall_verifier")

MQTT_BROKER_DEFAULT = "192.168.31.172"
MQTT_PORT_DEFAULT = 1883
TOPIC_FALL_ALERT = "baymax/fall_alert"
TOPIC_FALL_CONFIRMED = "baymax/fall_confirmed"
PATIENT_CODE_DEFAULT = "P-1042"

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Please install paho-mqtt: pip install paho-mqtt")
    sys.exit(1)


def speak_phrase(text: str):
    """Speaks through Raspberry Pi speaker using pyttsx3 or terminal fallback."""
    print(f"\n🤖 [Baymax Audio]: \"{text}\"")
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        logger.debug(f"TTS not available ({e})")


class BaymaxFallVerifier:
    def __init__(self, broker=MQTT_BROKER_DEFAULT, port=MQTT_PORT_DEFAULT, patient_code=PATIENT_CODE_DEFAULT):
        self.broker = broker
        self.port = port
        self.patient_code = patient_code
        self.client = None
        self.verification_thread = None
        self.cancel_event = threading.Event()
        self.running = True

    def _init_mqtt_client(self):
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except Exception:
            self.client = mqtt.Client()

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        logger.info(f"Connected to MQTT Broker at {self.broker}:{self.port}")
        client.subscribe(TOPIC_FALL_ALERT)
        logger.info(f"Subscribed to topic: {TOPIC_FALL_ALERT}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            payload = {}

        event = payload.get("event", "")
        code = payload.get("patient_code", self.patient_code)

        if event == "fall_detected":
            logger.warning(f"⚠️ [IMPACT ALERT] Fall detected from ESP32 for {code}!")
            self.start_verification(code)

    def start_verification(self, patient_code):
        """Starts 3-stage voice check-in."""
        self.cancel_event.clear()
        if self.verification_thread and self.verification_thread.is_alive():
            logger.info("Verification already active, resetting check timer...")
            return

        self.verification_thread = threading.Thread(
            target=self._run_verification,
            args=(patient_code,),
            daemon=True
        )
        self.verification_thread.start()

    def cancel_verification(self):
        """Cancels verification if user pressed button or said they are safe."""
        self.cancel_event.set()
        logger.info("Fall verification canceled by user.")

    def _run_verification(self, patient_code):
        print("\n" + "="*65)
        print(f"⚠️ [FALL VERIFICATION INITIATED] Patient {patient_code}")
        print("   Baymax will ask 3 times: 'Fall detected. Are you okay?'")
        print("="*65)

        for attempt in range(1, 4):
            if self.cancel_event.is_set():
                print("✅ Patient confirmed safe. Verification aborted.")
                return

            msg = "Fall detected. Are you okay?" if attempt == 1 else "Are you okay?"
            print(f"   -> [Attempt {attempt}/3] Asking: \"{msg}\"")
            speak_phrase(msg)

            # Wait 4 seconds per check
            for _ in range(8):
                if self.cancel_event.is_set():
                    print("✅ Patient confirmed safe. Verification aborted.")
                    return
                time.sleep(0.5)

        # If we reached here, patient failed to respond to all 3 checks
        print("\n" + "#"*65)
        print("🚨 [CRITICAL ALERT] No response from patient after 3 voice checks!")
        print("📞 CALLING THE GUARDIAN NOW... (+1-800-555-CARE)")
        print("   Emergency alert dispatched to Doctor & Guardian consoles.")
        print("#"*65 + "\n")

        # Publish confirmation to MQTT so server pushes red alert
        if self.client:
            confirm_payload = json.dumps({
                "event": "fall_confirmed",
                "patient_code": patient_code,
                "reason": "No response to 3 companion check-ins",
                "timestamp": time.time()
            })
            self.client.publish(TOPIC_FALL_CONFIRMED, confirm_payload)
            logger.info(f"Published fall confirmation to {TOPIC_FALL_CONFIRMED}")

    def run(self):
        self._init_mqtt_client()
        while self.running:
            try:
                logger.info(f"Connecting to MQTT Broker at {self.broker}:{self.port}...")
                self.client.connect(self.broker, self.port, 60)
                self.client.loop_forever()
            except Exception as e:
                logger.warning(f"MQTT connection error ({e}). Retrying in 3 seconds...")
                time.sleep(3)


_verifier_instance = None
_verifier_thread = None

def start_in_background(broker=MQTT_BROKER_DEFAULT, port=MQTT_PORT_DEFAULT, patient_code=PATIENT_CODE_DEFAULT):
    """Starts the Fall Verifier service in a daemon thread."""
    global _verifier_instance, _verifier_thread
    _verifier_instance = BaymaxFallVerifier(broker=broker, port=port, patient_code=patient_code)
    _verifier_thread = threading.Thread(target=_verifier_instance.run, daemon=True)
    _verifier_thread.start()
    logger.info("[FallVerifier] Background fall monitor started.")
    return _verifier_instance


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BaymaxCare Fall Verifier Client")
    parser.add_argument("--broker", default=MQTT_BROKER_DEFAULT, help="MQTT Broker IP")
    parser.add_argument("--port", type=int, default=MQTT_PORT_DEFAULT, help="MQTT Broker Port")
    parser.add_argument("--patient-code", default=PATIENT_CODE_DEFAULT, help="Patient Code")
    args = parser.parse_args()

    verifier = BaymaxFallVerifier(broker=args.broker, port=args.port, patient_code=args.patient_code)
    try:
        verifier.run()
    except KeyboardInterrupt:
        logger.info("Fall verifier shut down.")

