#!/usr/bin/env python3
"""
Baymax voice assistant - ElevenLabs Conversational AI version.

ElevenLabs now does everything in the cloud: speech recognition, the LLM brain, and the voice.
This file only handles the Raspberry Pi side: microphone in, speaker out, and starting / ending
a session.

Structure
    1. CONFIGURATION          agent id, API key, audio devices, sample rates
    2. RaspberryPiAudioInterface   mic -> ElevenLabs (resampled 44.1k -> 16k), ElevenLabs -> speaker
    3. BaymaxAssistant        activate() / deactivate() / shutdown()  (called by push_button.py)

Setup:
    export ELEVENLABS_API_KEY="your-key-here"
    pip install elevenlabs pyaudio

Run through main.py (buttons) - see main.py.
"""
import os
import queue
import threading
import time

import audioop          # Python <= 3.12 (on 3.13: pip install audioop-lts)
import pyaudio

from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, AudioInterface


# ==========================================================================
# 1. CONFIGURATION
# ==========================================================================
AGENT_ID = "agent_9601m6kbj3kcf55aqr12q6n9fc4m"

MIC_DEVICE_INDEX = 1          # find yours with: python3 -c "import pyaudio; p=pyaudio.PyAudio(); [print(i, p.get_device_info_by_index(i)['name']) for i in range(p.get_device_count())]"
SPEAKER_DEVICE_INDEX = 0

INPUT_RATE = 44100            # what the USB mic supports
OUTPUT_RATE = 16000           # what ElevenLabs sends back / expects (PCM 16 kHz)
CHANNELS = 1
FORMAT = pyaudio.paInt16
INPUT_FRAMES = 4410           # 100 ms of mic audio per callback
OUTPUT_SLICE = 2048           # bytes written to the speaker at a time (small = stops quickly)
MIC_HANGOVER = 0.6            # seconds the mic stays muted after Baymax's audio ends (gaps + echo tail)
INTRO_WAIT = 12.0             # max seconds the mic stays muted waiting for Baymax's first words


# ==========================================================================
# 2. AUDIO INTERFACE  (the SDK calls start / stop / output / interrupt)
# ==========================================================================
class RaspberryPiAudioInterface(AudioInterface):

    def __init__(self, mic_index=MIC_DEVICE_INDEX, speaker_index=SPEAKER_DEVICE_INDEX):
        self.mic_index = mic_index
        self.speaker_index = speaker_index

        self.pa = None
        self.input_stream = None
        self.output_stream = None
        self.input_callback = None
        self.output_thread = None

        self.output_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.resample_state = None

        # half-duplex gating: the mic is muted while Baymax talks
        self._playing = False            # worker is writing audio to the speaker right now
        self._heard_agent = False        # Baymax's audio has arrived at least once this session
        self._last_audio_end = 0.0
        self._session_start = 0.0
        self._mic_muted = False

    # ---- called by the SDK when the session starts
    def start(self, input_callback):
        self.input_callback = input_callback
        self.stop_event.clear()
        self.resample_state = None
        self._playing = False
        self._heard_agent = False
        self._last_audio_end = 0.0
        self._mic_muted = False
        self._session_start = time.time()
        self.pa = pyaudio.PyAudio()

        print("Opening USB microphone...")
        self.input_stream = self.pa.open(
            format=FORMAT, channels=CHANNELS, rate=INPUT_RATE, input=True,
            input_device_index=self.mic_index, frames_per_buffer=INPUT_FRAMES,
            stream_callback=self._on_mic_audio,
        )

        print("Opening speaker output...")
        self.output_stream = self.pa.open(
            format=FORMAT, channels=CHANNELS, rate=OUTPUT_RATE, output=True,
            output_device_index=self.speaker_index, frames_per_buffer=1000,
        )

        self.output_thread = threading.Thread(target=self._output_worker, daemon=True)
        self.output_thread.start()
        print("Microphone and speaker ready.")

    # ---- mic -> ElevenLabs
    def _on_mic_audio(self, in_data, frame_count, time_info, status):
        if in_data and not self.stop_event.is_set():
            converted, self.resample_state = audioop.ratecv(
                in_data, 2, CHANNELS, INPUT_RATE, OUTPUT_RATE, self.resample_state)
            if converted:
                talking = self._agent_is_talking()
                if talking != self._mic_muted:
                    self._mic_muted = talking
                    print("[Baymax] (mic muted - Baymax is speaking)" if talking
                          else "[Baymax] your turn - go ahead.")
                if talking:
                    converted = b"\x00" * len(converted)   # send silence, not echo or interruptions
                self.input_callback(converted)
        return (None, pyaudio.paContinue)

    # ---- is Baymax talking (or about to)? then the mic is muted
    def _agent_is_talking(self):
        if not self._heard_agent:
            # waiting for the intro: stay muted until it starts (give up after INTRO_WAIT seconds)
            return time.time() - self._session_start < INTRO_WAIT
        return (self._playing or not self.output_queue.empty()
                or time.time() - self._last_audio_end < MIC_HANGOVER)

    # ---- ElevenLabs -> speaker
    def output(self, audio):
        if audio and not self.stop_event.is_set():
            self._heard_agent = True
            self._last_audio_end = time.time()
            self.output_queue.put(audio)

    def _output_worker(self):
        while not self.stop_event.is_set():
            try:
                audio = self.output_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            self._playing = True
            try:
                for i in range(0, len(audio), OUTPUT_SLICE):
                    if self.stop_event.is_set():       # stop button / session ended
                        break
                    self.output_stream.write(audio[i:i + OUTPUT_SLICE])
            except Exception as e:
                if not self.stop_event.is_set():
                    print("Speaker error:", e)
            finally:
                self._playing = False
                self._last_audio_end = time.time()

    # ---- the user talked over the agent: drop whatever is queued
    def interrupt(self):
        while True:
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                break

    # ---- called by the SDK when the session ends
    def stop(self):
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self.interrupt()

        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()
            self.input_stream = None

        if self.output_thread:
            self.output_thread.join(timeout=2)         # worker exits within one slice
            self.output_thread = None

        if self.output_stream:
            self.output_stream.stop_stream()
            self.output_stream.close()
            self.output_stream = None

        if self.pa:
            self.pa.terminate()
            self.pa = None
        print("Audio interface stopped.")


# ==========================================================================
# 3. THE ASSISTANT  (what the buttons control)
# ==========================================================================
class BaymaxAssistant:

    def __init__(self, agent_id=AGENT_ID, mic_index=MIC_DEVICE_INDEX,
                 speaker_index=SPEAKER_DEVICE_INDEX):
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            raise SystemExit("ELEVENLABS_API_KEY environment variable is missing!\n"
                             "Run: export ELEVENLABS_API_KEY=\"your-key\"")
        self.client = ElevenLabs(api_key=api_key)
        self.agent_id = agent_id
        self.mic_index = mic_index
        self.speaker_index = speaker_index

        self._lock = threading.Lock()
        self._active = False
        self._conversation = None

    # ------------------------------------------------------------ button 1
    def activate(self):
        """Start a voice session (ignored if one is already running)."""
        with self._lock:
            if self._active:
                print("[Baymax] already listening - press the stop button to end the session.")
                return
            self._active = True
        # run in its own thread so the button callback returns immediately
        threading.Thread(target=self._run_session, name="elevenlabs-session", daemon=True).start()

    # ------------------------------------------------------------ button 2
    def deactivate(self):
        """End the session right now: speech stops, mic closes."""
        with self._lock:
            conv = self._conversation
        if conv is None:
            print("[Baymax] no active session.")
            return
        try:
            conv.end_session()
        except Exception as e:
            print("[Baymax] end_session:", e)

    def shutdown(self):
        self.deactivate()

    def is_active(self):
        """True while a voice session is running."""
        with self._lock:
            return self._active

    # ------------------------------------------------------------ one session
    def _run_session(self):
        audio = RaspberryPiAudioInterface(self.mic_index, self.speaker_index)
        conv = Conversation(
            client=self.client,
            agent_id=self.agent_id,
            requires_auth=True,
            audio_interface=audio,
            callback_agent_response=lambda text: print(f"\n🤖 Baymax: {text}"),
            callback_user_transcript=lambda text: print(f"\n🧑 You: {text}"),
        )
        with self._lock:
            self._conversation = conv
        try:
            print("\n[Baymax] connecting to ElevenLabs...")
            conv.start_session()
            print("[Baymax] connected - start speaking.")
            conv.wait_for_session_end()
        except Exception as e:
            print("[Baymax] conversation error:", e)
        finally:
            audio.stop()                               # safe to call twice
            with self._lock:
                self._conversation = None
                self._active = False
            print("[Baymax] session ended. Press button 1 to talk again.")
