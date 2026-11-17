#!/usr/bin/env python3
"""
Main file: Multithreaded BaymaxCare In-Home Companion
Orchestrates:
  1. Live Camera Stream (cam_server.py / server_cam.py on port 5000)
  2. "Hey Baymax" Hotword Listener (hotword.py)
  3. Physical Push Buttons (push_button.py on GPIO 17 & 27)
  4. Local ElevenLabs Conversational Voice Assistant (baymax_assistant.py)
  5. ESP32-C3 MPU-6050 Fall Detection via MQTT (mqtt_fall_listener.py on broker.hivemq.com)

Microphone Arbitration:
  - Idle               : Hotword listener monitors for "Hey Baymax" OR button 1 is pressed.
  - Fall Alert         : When ESP32 detects fall, interrupts conversation to check on patient.
  - Local Talking      : ElevenLabs assistant session runs when activated by user.
  - Finished           : Mic and speaker cleanly release back to idle hotword listening.

Run:
    python3 main.py --cam-port 5000
    python3 main.py --no-hotword
    python3 main.py --no-camera
    python3 main.py --no-fall
"""

import argparse
import threading
import time
import sys

# Attempt importing Baymax Assistant (with fallback for testing environments)
try:
    from baymax_assistant import (BaymaxAssistant, AGENT_ID,
                                  MIC_DEVICE_INDEX, SPEAKER_DEVICE_INDEX)
    ASSISTANT_AVAILABLE = True
except ImportError:
    ASSISTANT_AVAILABLE = False
    AGENT_ID = "sample_agent"
    MIC_DEVICE_INDEX = None
    SPEAKER_DEVICE_INDEX = None

    class BaymaxAssistant:
        def __init__(self, *args, **kwargs):
            self._active = False
        def activate(self):
            self._active = True
            print("[Baymax Assistant] (Simulated) Activated")
        def deactivate(self):
            self._active = False
            print("[Baymax Assistant] (Simulated) Deactivated")
        def is_active(self):
            return self._active
        def shutdown(self):
            self._active = False


def main():
    ap = argparse.ArgumentParser(description="Multithreaded BaymaxCare In-Home Companion")

    # ElevenLabs session
    ap.add_argument("--agent-id", default=AGENT_ID)
    ap.add_argument("--mic", type=int, default=MIC_DEVICE_INDEX, help="PyAudio input device index")
    ap.add_argument("--speaker", type=int, default=SPEAKER_DEVICE_INDEX, help="PyAudio output device index")

    # Hotword
    ap.add_argument("--model", default="hey_baymax.tflite", help="path to hey_baymax.tflite")
    ap.add_argument("--no-hotword", action="store_true", help="buttons only")
    ap.add_argument("--threshold", type=float, default=0.8, help="hotword sensitivity (lower = more sensitive)")
    ap.add_argument("--window", type=int, default=5, help="inferences averaged for hotword")
    ap.add_argument("--hotword-device", default=None, help="sounddevice input index/name for hotword")
    ap.add_argument("--cooldown", type=float, default=2.0, help="seconds hotword is ignored after mic opens")

    # Buttons
    ap.add_argument("--pin-activate", type=int, default=17, help="BCM pin of button 1 (start)")
    ap.add_argument("--pin-deactivate", type=int, default=27, help="BCM pin of button 2 (stop)")
    ap.add_argument("--start-now", action="store_true", help="start assistant session immediately on boot")

    # Camera Server
    ap.add_argument("--no-camera", action="store_true", help="do not start the guardian live-view server")
    ap.add_argument("--cam-port", type=int, default=5000, help="port of the camera live-view web server")

    # ESP32 Fall Detection via MQTT
    ap.add_argument("--no-fall", action="store_true", help="disable ESP32 MQTT fall detection")
    ap.add_argument("--mqtt-broker", default="broker.hivemq.com", help="MQTT Broker for ESP32 fall detection")
    ap.add_argument("--mqtt-port", type=int, default=1883, help="MQTT Broker port")
    ap.add_argument("--mqtt-topic", default="baymaxcare/P-1042/fall", help="MQTT topic for fall events")

    args = ap.parse_args()

    quit_event = threading.Event()
    start_requested = threading.Event()

    print("==========================================================")
    print("      BaymaxCare In-Home Companion Orchestrator          ")
    if not args.no_camera:
        print(f"      Camera Port  : {args.cam_port}")
    if not args.no_fall:
        print(f"      Fall Detector: {args.mqtt_broker}:{args.mqtt_port} ({args.mqtt_topic})")
    print("==========================================================")

    # 1. Initialize Local Voice Assistant
    assistant = BaymaxAssistant(args.agent_id, args.mic, args.speaker)

    # 2. Start Guardian Camera Server in Background Thread
    cam = None
    if not args.no_camera:
        try:
            try:
                import cam_server as cam_module
            except ImportError:
                import server_cam as cam_module
            cam_module.start_in_background(port=args.cam_port)
            cam = cam_module
        except Exception as e:
            print(f"[Camera] not available ({e}) -> continuing without live view.")

    # 3. Start ESP32 MQTT Fall Detection Listener in Background Thread
    fall_listener = None
    if not args.no_fall:
        try:
            import mqtt_fall_listener

            def on_fall_detected():
                # Yield audio if local assistant is active
                if assistant.is_active():
                    assistant.deactivate()
                # Run the 3-step check and guardian emergency alert
                mqtt_fall_listener.handle_fall_event()

            fall_listener = mqtt_fall_listener.start_in_background(
                broker=args.mqtt_broker,
                port=args.mqtt_port,
                topic=args.mqtt_topic,
                on_fall_detected=on_fall_detected
            )
            print("[FallDetector] MQTT Fall Listener active in background.")
        except Exception as e:
            print(f"[FallDetector] not available ({e}) -> continuing without MQTT fall detection.")

    # 4. Initialize Hotword Listener
    listener = None
    if not args.no_hotword:
        try:
            from hotword import HotwordListener
            listener = HotwordListener(args.model, args.threshold, args.window,
                                       args.hotword_device, args.cooldown)
            print("[Hotword] Listener initialized for 'Hey Baymax'")
        except Exception as e:
            print(f"[Hotword] not available ({e}) -> buttons only.")

    # 5. Push Buttons Monitor (gpiozero thread)
    def on_button_start():
        if assistant.is_active():
            print("[Baymax] already talking - press button 2 to stop.")
        else:
            start_requested.set()

    buttons = None
    try:
        from push_button import PushButtons
        buttons = PushButtons(on_button_start, assistant.deactivate,
                              args.pin_activate, args.pin_deactivate)
        buttons.start()
        print(f"[Buttons] GPIO pins {args.pin_activate} (start) and {args.pin_deactivate} (stop) active.")
    except Exception as e:
        print(f"[Buttons] not available ({e}) -> hotword / remote triggers only.")

    if args.start_now:
        start_requested.set()

    # 6. Main Orchestration Loop
    try:
        while not quit_event.is_set():
            # Hotword listening when idle
            if listener is not None and not start_requested.is_set():
                try:
                    heard = listener.listen(lambda: start_requested.is_set() or quit_event.is_set())
                    if heard:
                        start_requested.set()
                except Exception as e:
                    print(f"[Hotword] Error during listening ({e}) -> falling back to push buttons.")
                    listener = None

            if not start_requested.wait(timeout=0.3):
                continue  # Nothing requested yet

            start_requested.clear()

            time.sleep(0.2)  # Mic settle delay
            assistant.activate()
            while assistant.is_active() and not quit_event.is_set():
                time.sleep(0.2)
            start_requested.clear()  # Ignore presses made during the session

    except KeyboardInterrupt:
        print("\n[BaymaxCare] Shutting down companion services...")
    finally:
        quit_event.set()
        assistant.shutdown()
        if cam is not None:
            try: cam.shutdown()
            except Exception: pass
        if fall_listener is not None:
            try: fall_listener.shutdown()
            except Exception: pass
        if buttons is not None:
            try: buttons.stop()
            except Exception: pass
        time.sleep(0.5)
        print("[BaymaxCare] Companion safely stopped.")


if __name__ == "__main__":
    main()
