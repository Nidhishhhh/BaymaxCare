import io
import time
from flask import Flask, Response, jsonify
from picamera2 import Picamera2

app = Flask(__name__)

# Initialize Picamera2
picam2 = Picamera2()
config = picam2.create_video_configuration(main={"size": (640, 480)})
picam2.configure(config)

is_streaming = False

def generate_frames():
    global is_streaming
    while is_streaming:
        # Capture JPEG to an in-memory byte buffer
        stream = io.BytesIO()
        picam2.capture_file(stream, format='jpeg')
        frame_bytes = stream.getvalue()
        
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        time.sleep(0.04)  # ~25 FPS

@app.route('/start', methods=['GET', 'POST'])
def start_camera():
    global is_streaming
    if not is_streaming:
        picam2.start()
        is_streaming = True
    return jsonify({"status": "started"})

@app.route('/stop', methods=['GET', 'POST'])
def stop_camera():
    global is_streaming
    if is_streaming:
        is_streaming = False
        time.sleep(0.2)
        picam2.stop()
    return jsonify({"status": "stopped"})

@app.route('/video_feed')
def video_feed():
    global is_streaming
    if not is_streaming:
        picam2.start()
        is_streaming = True
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
