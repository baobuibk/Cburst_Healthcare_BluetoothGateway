import json
import time
import os
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
import redis
from dotenv import load_dotenv

load_dotenv()

WINDOW_SIZE = int(os.getenv("WINDOW_SIZE", 10))
RSSI_THRESHOLD = int(os.getenv("RSSI_THRESHOLD", -90))
FREQ_THRESHOLD = int(os.getenv("FREQ_THRESHOLD", 3))
MAX_FREQ = int(os.getenv("MAX_FREQ", 10))
W1 = float(os.getenv("W1", 0.5))
W2 = float(os.getenv("W2", 0.5))
QUEUE_HOST = os.getenv("QUEUE_HOST", "localhost")
QUEUE_PORT = int(os.getenv("QUEUE_PORT", 6379))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", min(os.cpu_count() * 2, 10)))
MAX_BUFFER_PER_BEACON = int(os.getenv("MAX_BUFFER_PER_BEACON", 100))

redis_client = redis.Redis(host=QUEUE_HOST, port=QUEUE_PORT, db=0)

beacon_data = defaultdict(lambda: defaultdict(lambda: deque(maxlen=MAX_BUFFER_PER_BEACON)))
beacon_state = {}

def calculate_score(rssi_avg, freq):
    rssi_normalized = 100 - abs(rssi_avg)
    freq_normalized = min(freq, MAX_FREQ) / MAX_FREQ
    return (W1 * rssi_normalized) + (W2 * freq_normalized * 100)

def process_beacon(beacon_id):
    scores = {}
    current_time = time.time()
    for gw in beacon_data[beacon_id]:
        rssi_list = [r for r, t in beacon_data[beacon_id][gw]]
        if len(rssi_list) >= FREQ_THRESHOLD:
            rssi_avg = sum(rssi_list) / len(rssi_list)
            if rssi_avg > RSSI_THRESHOLD:
                scores[gw] = calculate_score(rssi_avg, len(rssi_list))
    
    if scores:
        nearest_gw = max(scores, key=scores.get)
        ts = beacon_data[beacon_id][nearest_gw][-1][1]
        if beacon_id not in beacon_state or beacon_state[beacon_id]["gateway"] != nearest_gw:
            redis_client.rpush("aws_queue", json.dumps({
                "beacon_id": beacon_id,
                "gateway_id": nearest_gw,
                "detected": 1,
                "timestamp": ts
            }))
            beacon_state[beacon_id] = {"gateway": nearest_gw, "start_time": ts, "last_time": ts}
            redis_client.hset("beacon_state", beacon_id, json.dumps(beacon_state[beacon_id]))
        else:
            beacon_state[beacon_id]["last_time"] = ts
            redis_client.hset("beacon_state", beacon_id, json.dumps(beacon_state[beacon_id]))
    else:
        if beacon_id in beacon_state:
            redis_client.rpush("aws_queue", json.dumps({
                "beacon_id": beacon_id,
                "gateway_id": beacon_state[beacon_id]["gateway"],
                "detected": 0,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")
            }))
            del beacon_state[beacon_id]
            redis_client.hdel("beacon_state", beacon_id)

def main():
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        while True:
            start_time = time.time()
            temp_data = set()
            for _ in range(min(redis_client.llen("beacon_data"), 1000)):
                data = redis_client.lpop("beacon_data")
                if not data:
                    break
                data = json.loads(data.decode())
                beacon_id = data["beacon_id"]
                gateway_id = data["gateway_id"]
                rssi = data["rssi"]
                ts = data["timestamp"]
                beacon_data[beacon_id][gateway_id].append((rssi, ts))
                temp_data.add(beacon_id)
            
            current_time = time.time()
            expired_beacons = []
            for beacon_id in beacon_data:
                for gw in list(beacon_data[beacon_id].keys()):
                    beacon_data[beacon_id][gw] = deque(
                        [(r, t) for r, t in beacon_data[beacon_id][gw]
                         if current_time - time.strptime(t, "%Y-%m-%dT%H:%M:%S").timestamp() <= WINDOW_SIZE],
                        maxlen=MAX_BUFFER_PER_BEACON
                    )
                    if not beacon_data[beacon_id][gw]:
                        del beacon_data[beacon_id][gw]
                if not beacon_data[beacon_id]:
                    expired_beacons.append(beacon_id)
            for beacon_id in expired_beacons:
                del beacon_data[beacon_id]
            
            if temp_data:
                executor.map(process_beacon, temp_data)
            
            elapsed_time = time.time() - start_time
            time.sleep(max(0, 1 - elapsed_time))

if __name__ == "__main__":
    main()