import paho.mqtt.client as mqtt
import json
import os
import redis
from dotenv import load_dotenv

load_dotenv()

BROKER_HOST = os.getenv("BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))
QUEUE_HOST = os.getenv("QUEUE_HOST", "localhost")
QUEUE_PORT = int(os.getenv("QUEUE_PORT", 6379))

redis_client = redis.Redis(host=QUEUE_HOST, port=QUEUE_PORT, db=0)

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"Connected to {BROKER_HOST}:{BROKER_PORT}")
        client.subscribe("bluetooth/+/data")
    else:
        print(f"Connection failed with code {rc}")

def on_message(client, userdata, msg):
    topic = msg.topic
    payload = json.loads(msg.payload.decode())
    gateway_id = topic.split("/")[1]  # Lấy gateway_id từ topic
    beacon_id = payload["beacon_id"]
    rssi = payload["rssi"]
    ts = payload["timestamp"]
    redis_client.rpush("beacon_data", json.dumps({
        "beacon_id": beacon_id,
        "gateway_id": gateway_id,
        "rssi": rssi,
        "timestamp": ts
    }))
    redis_client.rpush("logs", json.dumps({
        "time": ts,
        "service": "Listener",
        "message": f"Pushed: {beacon_id}, {gateway_id}"
    }))
    print(f"Pushed to queue: {beacon_id}, {gateway_id}")

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

try:
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_forever()
except ConnectionRefusedError:
    print(f"Failed to connect to {BROKER_HOST}:{BROKER_PORT}. Ensure NanoMQ is running.")
except Exception as e:
    print(f"Error: {e}")