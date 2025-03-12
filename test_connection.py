import paho.mqtt.client as mqtt
client = mqtt.Client()
client.connect("localhost", 1883, 60)
print("Connected to NanoMQ")