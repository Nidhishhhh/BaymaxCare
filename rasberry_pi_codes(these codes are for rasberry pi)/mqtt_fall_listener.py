#!/usr/bin/env python3
"""
BaymaxCare: Raspberry Pi MQTT Fall Listener
Listens for fall events from ESP32 on topic 'baymax/fall'.
When a fall is received:
  1. Speaks "Fall detected. Are you okay?" 3 times through the speaker.
  2. If no response after 3 times, prints the emergency guardian call to the terminal.

Run on Raspberry Pi:
    python3 mqtt_fall_listener.py
    (or specify broker: python3 mqtt_fall_listener.py --broker 192.168.31.231)
"""

import time
import argparse
import sys
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


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"✔ Connected to MQTT Broker successfully!")
        client.subscribe(TOPIC_FALL)
        print(f"✔ Subscribed to topic: '{TOPIC_FALL}'")
        print("Waiting for fall events from ESP32...\n")
    else:
        print(f"❌ Connection failed with result code {rc}")


def on_message(client, userdata, msg):
    payload = msg.payload.decode().strip()
    print(f"\n[MQTT Incoming] Topic: {msg.topic} -> Message: '{payload}'")

    if "FALL" in payload.upper():
        handle_fall_event()


def handle_fall_event():
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


def main():
    parser = argparse.ArgumentParser(description="Baymax Raspberry Pi Fall Listener")
    parser.add_argument("--broker", default=DEFAULT_BROKER, help="MQTT Broker IP (default: localhost or broker.hivemq.com)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="MQTT Port (default: 1883)")
    args = parser.parse_args()

    print("==================================================")
    print("      BaymaxCare Raspberry Pi Fall Listener       ")
    print(f"      Broker: {args.broker}:{args.port}")
    print(f"      Topic : {TOPIC_FALL}")
    print("==================================================")

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except Exception:
        client = mqtt.Client()

    client.on_connect = on_connect
    client.on_message = on_message

    while True:
        try:
            print(f"Connecting to MQTT Broker at {args.broker}:{args.port}...")
            client.connect(args.broker, args.port, 60)
            client.loop_forever()
        except Exception as e:
            print(f"⚠ Connection error ({e}). Retrying in 3 seconds...")
            time.sleep(3)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nListener stopped.")

