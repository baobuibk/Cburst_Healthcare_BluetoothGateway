from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from flask_socketio import SocketIO, emit
import redis
import json
import os
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from collections import defaultdict
import time
import threading
from functools import wraps
import hashlib

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key")
socketio = SocketIO(app)

load_dotenv()
BROKER_HOST = os.getenv("BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))
QUEUE_HOST = os.getenv("QUEUE_HOST", "localhost")
QUEUE_PORT = int(os.getenv("QUEUE_PORT", 6379))
redis_client = redis.Redis(host=QUEUE_HOST, port=QUEUE_PORT, db=0)

mqtt_client = mqtt.Client()
mqtt_client.connect(BROKER_HOST, BROKER_PORT, 60)
mqtt_client.loop_start()

gateways = [
    {"id": "GW1", "ip": "192.168.1.1", "status": "Online", "beacons": 0},
    {"id": "GW2", "ip": "192.168.1.2", "status": "Online", "beacons": 0},
    {"id": "GW3", "ip": "192.168.1.3", "status": "Offline", "beacons": 0}
]

def init_users():
    if not redis_client.exists("users"):
        redis_client.hset("users", "admin", hashlib.md5("admin123".encode()).hexdigest())

init_users()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        stored_password = redis_client.hget("users", username)
        if stored_password and stored_password.decode() == hashlib.md5(password.encode()).hexdigest():
            session['username'] = username
            return redirect(url_for('dashboard'))
        return render_template('login.html', error="Invalid credentials")
    return render_template('login.html')

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_password = request.form['old_password']
        new_password = request.form['new_password']
        username = session['username']
        stored_password = redis_client.hget("users", username).decode()
        if hashlib.md5(old_password.encode()).hexdigest() == stored_password:
            redis_client.hset("users", username, hashlib.md5(new_password.encode()).hexdigest())
            return redirect(url_for('dashboard'))
        return render_template('change_password.html', error="Old password incorrect")
    return render_template('change_password.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

def update_realtime_data():
    while True:
        msg_rate = redis_client.llen("beacon_data")
        aws_rate = redis_client.llen("aws_queue")
        beacons_detected = len(redis_client.hkeys("beacon_state") or [])
        system_status = {
            "gateways": len(gateways),
            "gateways_online": sum(1 for gw in gateways if gw["status"] == "Online"),
            "beacons_detected": beacons_detected,
            "broker": "Online" if mqtt_client.is_connected() else "Offline",
            "redis": "Online" if redis_client.ping() else "Offline",
            "listener": "Running",
            "processor": "Running",
            "publisher": "Running",
            "alerts": ["GW3 offline for 5 minutes"] if any(gw["status"] == "Offline" for gw in gateways) else []
        }
        socketio.emit('update_dashboard', {
            'status': system_status,
            'msg_rate': msg_rate,
            'aws_rate': aws_rate
        })
        time.sleep(1)

threading.Thread(target=update_realtime_data, daemon=True).start()

@app.route('/api/dashboard', methods=['GET'])
@login_required
def api_dashboard():
    msg_rate = redis_client.llen("beacon_data")
    aws_rate = redis_client.llen("aws_queue")
    beacons_detected = len(redis_client.hkeys("beacon_state") or [])
    system_status = {
        "gateways": len(gateways),
        "gateways_online": sum(1 for gw in gateways if gw["status"] == "Online"),
        "beacons_detected": beacons_detected,
        "broker": "Online" if mqtt_client.is_connected() else "Offline",
        "redis": "Online" if redis_client.ping() else "Offline",
        "listener": "Running",
        "processor": "Running",
        "publisher": "Running",
        "alerts": ["GW3 offline for 5 minutes"] if any(gw["status"] == "Offline" for gw in gateways) else []
    }
    return jsonify({
        "status": system_status,
        "msg_rate": msg_rate,
        "aws_rate": aws_rate
    })

@app.route('/')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/gateways', methods=['GET'])
@login_required
def api_gateways():
    status_filter = request.args.get('status')
    id_filter = request.args.get('id', '').lower()
    filtered_gateways = gateways
    if status_filter:
        filtered_gateways = [gw for gw in filtered_gateways if gw["status"] == status_filter]
    if id_filter:
        filtered_gateways = [gw for gw in filtered_gateways if id_filter in gw["id"].lower()]
    for gw in filtered_gateways:
        gw["beacons"] = sum(1 for state in redis_client.hvals("beacon_state") 
                           if json.loads(state.decode())["gateway"] == gw["id"])
    return jsonify(filtered_gateways)

@app.route('/api/gateways/control/<gateway_id>', methods=['POST'])
@login_required
def api_control_gateway(gateway_id):
    action = request.json.get('action')
    for gw in gateways:
        if gw["id"] == gateway_id:
            gw["status"] = "Online" if action == "on" else "Offline"
            mqtt_client.publish(f"control/{gateway_id}", json.dumps({"action": action}))
            return jsonify({"success": True, "gateway": gw})
    return jsonify({"success": False, "error": "Gateway not found"}), 404

@app.route('/gateways')
@login_required
def gateways_list():
    return render_template('gateways.html')
@app.route('/api/beacons/<beacon_id>', methods=['DELETE'])
@login_required
def delete_beacon(beacon_id):
    # Logic xóa Beacon (ví dụ: xóa khỏi beacon_state)
    redis_client.hdel("beacon_state", beacon_id)
    return jsonify({"success": True, "message": f"Deleted {beacon_id}"})

@app.route('/api/beacons/<beacon_id>', methods=['PUT'])
@login_required
def edit_beacon(beacon_id):
    data = request.json
    # Logic chỉnh sửa (ví dụ: cập nhật gateway)
    current_state = redis_client.hget("beacon_state", beacon_id)
    if current_state:
        state = json.loads(current_state.decode())
        state["gateway"] = data.get("gateway", state["gateway"])
        redis_client.hset("beacon_state", beacon_id, json.dumps(state))
        return jsonify({"success": True, "message": f"Updated {beacon_id}"})
    return jsonify({"success": False, "error": "Beacon not found"}), 404
@app.route('/api/beacons', methods=['GET'])
@login_required
def api_beacons():
    gateway_filter = request.args.get('gateway')
    detected_filter = request.args.get('detected')
    id_filter = request.args.get('id', '').lower()
    beacons = {}
    for data in redis_client.lrange("beacon_data", -100, -1):
        d = json.loads(data.decode())
        beacon_id = d["beacon_id"]
        detected = 1 if redis_client.hexists("beacon_state", beacon_id) else 0
        beacons[beacon_id] = {
            "gateway": d["gateway_id"],
            "rssi": d["rssi"],
            "last_seen": d["timestamp"],
            "detected": detected
        }
    filtered_beacons = beacons
    if gateway_filter:
        filtered_beacons = {k: v for k, v in filtered_beacons.items() if v["gateway"] == gateway_filter}
    if detected_filter is not None:
        detected_filter = int(detected_filter)
        filtered_beacons = {k: v for k, v in filtered_beacons.items() if v["detected"] == detected_filter}
    if id_filter:
        filtered_beacons = {k: v for k, v in filtered_beacons.items() if id_filter in k.lower()}
    return jsonify(filtered_beacons)

@app.route('/beacons')
@login_required
def beacons_list():
    return render_template('beacons.html')

@app.route('/api/config', methods=['GET', 'POST'])
@login_required
def api_config():
    config_keys = [
        "WINDOW_SIZE", "RSSI_THRESHOLD", "FREQ_THRESHOLD", "MAX_FREQ",
        "W1", "W2", "MAX_WORKERS", "MAX_BUFFER_PER_BEACON",
        "QUEUE_HOST", "QUEUE_PORT"
    ]
    if request.method == 'GET':
        config = {key: redis_client.get(f"config:{key}").decode() if redis_client.exists(f"config:{key}")
                  else os.getenv(key, "") for key in config_keys}
        return jsonify(config)
    elif request.method == 'POST':
        for key, value in request.json.items():
            if key in config_keys:
                redis_client.set(f"config:{key}", value)
        return jsonify({"success": True})

@app.route('/config')
@login_required
def config():
    return render_template('config.html')

@app.route('/api/logs', methods=['GET'])
@login_required
def api_logs():
    service_filter = request.args.get('service')
    time_filter = request.args.get('time')
    logs = [json.loads(log.decode()) for log in redis_client.lrange("logs", -50, -1)]
    if service_filter:
        logs = [log for log in logs if log["service"] == service_filter]
    if time_filter:
        logs = [log for log in logs if time_filter in log["time"]]
    stats = {"processing_time": "50ms", "latency": "200ms"}
    return jsonify({"logs": logs, "stats": stats})

@app.route('/logs')
@login_required
def logs():
    return render_template('logs.html')

@socketio.on('connect')
def handle_connect():
    if 'username' not in session:
        return False
    print("Client connected")

if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=5000)