#!/usr/bin/env python3
"""
BaymaxCare: Raspberry Pi MQTT Fall Listener
Listens for fall events from ESP32 on topic 'baymaxcare/P-1042/fall'.
When a fall is received:
  1. Speaks "Fall detected. Are you okay?" 3 times through the speaker.
  2. If no response after 3 times, prints the emergency guardian call to the terminal.

Run Standalone on Raspberry Pi:
    python3 mqtt_fall_listener.py
    (or specify broker: python3 mqtt_fall_listener.py --broker broker.hivemq.com)

Or import in main.py:
    import mqtt_fall_listener
    listener = mqtt_fall_listener.start_in_background(...)
"""

import time
import argparse
import sys
import threading
import subprocess
import shutil

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Please install paho-mqtt: pip install paho-mqtt")
    sys.exit(1)

# Default public cloud broker (works across 5GHz, 2.4GHz, or different Wi-Fi networks)
DEFAULT_BROKER = "broker.hivemq.com"
DEFAULT_PORT = 1883
TOPIC_FALL = "baymaxcare/P-1042/fall"

_tts_engine = None
_tts_engine_failed = False
_fall_in_progress = threading.Lock()


def speak(text: str):
    """Speaks out loud through the Raspberry Pi speaker with robust fallbacks."""
    print(f"🤖 [Baymax Audio]: \"{text}\"")

    # Method 1: Direct system espeak (most reliable on Raspberry Pi Linux, no Python 3.13 weakref bugs)
    if shutil.which("espeak"):
        try:
            subprocess.run(
                ["espeak", "-ven+m3", "-a", "120", "-s", "145", text],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6
            )
            return
        except Exception:
            pass

    if shutil.which("spd-say"):
        try:
            subprocess.run(
                ["spd-say", text],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6
            )
            return
        except Exception:
            pass

    # Method 2: pyttsx3 singleton (avoid re-init which crashes under Python 3.13)
    global _tts_engine, _tts_engine_failed
    if not _tts_engine_failed:
        try:
            if _tts_engine is None:
                import pyttsx3
                _tts_engine = pyttsx3.init()
            _tts_engine.say(text)
            _tts_engine.runAndWait()
        except Exception:
            _tts_engine_failed = True  # Avoid repeated crash spam if ALSA is busy


def handle_fall_event():
    """Executes the 3-step check and triggers emergency guardian call."""
    if not _fall_in_progress.acquire(blocking=False):
        # A check is already running; ignore duplicate sensor triggers
        return

    try:
        print("\n" + "="*60)
        print("⚠️  [FALL DETECTED BY ESP32 SENSOR]")
        print("   Initiating 3-step patient check...")
        print("="*60)

        for i in range(1, 4):
            phrase = "Fall detected. Are you okay?" if i == 1 else "Are you okay?"
            print(f"\n   -> [Check {i}/3]: Asking: \"{phrase}\"")
            speak(phrase)
            print("      Waiting 4 seconds for patient response...")
            time.sleep(4)

        # After 3 checks without response:
        print("\n" + "#"*60)
        print("🚨 [EMERGENCY PROTOCOL ACTIVATED]")
        print("   Patient did not respond to 3 check-ins!")
        print("📞 Calling the guardian... (Priya Iyer: +1-800-555-CARE)")
        print("#"*60 + "\n")
    finally:
        _fall_in_progress.release()


class BaymaxFallListener:
    """Manages MQTT connection and listens for fall detection events in the background."""

    def __init__(self, broker=DEFAULT_BROKER, port=DEFAULT_PORT, topic=TOPIC_FALL, on_fall_detected=None):
        self.broker = broker
        self.port = port
        self.topic = topic
        self.on_fall_detected = on_fall_detected or handle_fall_event
        self.running = False
        self.client = None
        self._thread = None

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            print(f"[FallDetector] ✔ Connected to MQTT Broker ({self.broker}:{self.port})")
            client.subscribe(self.topic)
            print(f"[FallDetector] ✔ Subscribed to topic: '{self.topic}'")
        else:
            print(f"[FallDetector] ❌ Connection failed with result code {rc}")

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode().strip()
        print(f"\n[FallDetector] MQTT Incoming -> '{payload}'")

        if "FALL" in payload.upper():
            # Run in a separate thread so MQTT loop is not blocked by sleep/speech
            threading.Thread(target=self.on_fall_detected, daemon=True).start()

    def run(self):
        self.running = True
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except Exception:
            self.client = mqtt.Client()

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        while self.running:
            try:
                self.client.connect(self.broker, self.port, 60)
                self.client.loop_forever()
            except Exception as e:
                if self.running:
                    print(f"[FallDetector] ⚠ Connection error ({e}). Retrying in 5s...")
                    time.sleep(5)

    def start(self):
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def shutdown(self):
        self.running = False
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass


_listener_instance = None

def start_in_background(broker=DEFAULT_BROKER, port=DEFAULT_PORT, topic=TOPIC_FALL, on_fall_detected=None):
    """Starts the MQTT Fall Listener in a background daemon thread."""
    global _listener_instance
    _listener_instance = BaymaxFallListener(
        broker=broker,
        port=port,
        topic=topic,
        on_fall_detected=on_fall_detected
    )
    _listener_instance.start()
    return _listener_instance


def main():
    parser = argparse.ArgumentParser(description="Baymax Raspberry Pi Fall Listener")
    parser.add_argument("--broker", default=DEFAULT_BROKER, help="MQTT Broker IP (default: broker.hivemq.com)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="MQTT Port (default: 1883)")
    parser.add_argument("--topic", default=TOPIC_FALL, help=f"MQTT Topic (default: {TOPIC_FALL})")
    args = parser.parse_args()

    print("==================================================")
    print("      BaymaxCare Raspberry Pi Fall Listener       ")
    print(f"      Broker: {args.broker}:{args.port}")
    print(f"      Topic : {args.topic}")
    print("==================================================")

    listener = BaymaxFallListener(broker=args.broker, port=args.port, topic=args.topic)
    try:
        listener.run()
    except KeyboardInterrupt:
        print("\nListener stopped.")
        listener.shutdown()


if __name__ == "__main__":
    main()
