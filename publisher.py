import json
import time
import os
import boto3
import redis
from dotenv import load_dotenv

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
AWS_IOT_ENDPOINT = os.getenv("AWS_IOT_ENDPOINT", "your-iot-endpoint.amazonaws.com")
AWS_TOPIC = os.getenv("AWS_TOPIC", "beacon/status")
QUEUE_HOST = os.getenv("QUEUE_HOST", "localhost")
QUEUE_PORT = int(os.getenv("QUEUE_PORT", 6379))

redis_client = redis.Redis(host=QUEUE_HOST, port=QUEUE_PORT, db=0)
aws_client = boto3.client('iot-data', region_name=AWS_REGION, endpoint_url=f"https://{AWS_IOT_ENDPOINT}")

def main():
    while True:
        if redis_client.llen("aws_queue") > 0:
            data = redis_client.lpop("aws_queue")
            if data:
                data = json.loads(data.decode())
                aws_client.publish(AWS_TOPIC, json.dumps(data), qos=1)
                print(f"Sent to AWS: {data}")
        time.sleep(0.1)

if __name__ == "__main__":
    main()