"""Synthetic app logs only: a stable dev API and a slower prod API."""
import json
import os
import time

environment = os.getenv('DEMO_ENVIRONMENT','dev')
counter = 0
while True:
    counter += 1
    slow = environment == 'prod' and counter % 4 == 0
    print(json.dumps({'service':'api','level':'error' if slow else 'info',
        'message':'database timeout while checking out' if slow else 'checkout request completed',
        'duration_ms':850 if slow else 200,'status':504 if slow else 200,
        'release':'demo-v2' if environment=='prod' else 'demo-v1',
        'environment':environment}), flush=True)
    time.sleep(2)
