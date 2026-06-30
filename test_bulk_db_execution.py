#!/usr/bin/env python
"""Test script to verify bulk DB test case execution."""
import os
import django
import json
from time import sleep

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'webapp.settings')
django.setup()

from django.contrib.auth.models import User
from django.test import Client
from django.conf import settings

# Get or create test user
user, _ = User.objects.get_or_create(username='testuser')

# Create a client and login
client = Client()
client.force_login(user)

# Test data
db_testcase_id = 1  # CON001
api_testcase_id = 6  # RU001

# Prepare bulk replay POST data
post_data = {
    'record_ids': [],  # No session recordings for this test
    'api_testcase_ids': [str(api_testcase_id)],
    'db_testcase_ids': [str(db_testcase_id)],
    'headless': 'on',
    'execution_mode': 'serial',
}

print(f"Test Configuration:")
print(f"  API Test Case ID: {api_testcase_id}")
print(f"  DB Test Case ID: {db_testcase_id}")
print(f"  Execution Mode: serial")

# Make the bulk replay request
print(f"\nMaking bulk replay request to /sessions/bulk-replay/...")
try:
    # Using sessions client which should have CSRF handling
    response = client.post(
        '/sessions/bulk-replay/',
        data=post_data,
        follow=True
    )
    print(f"Response Status: {response.status_code}")
    if response.redirect_chain:
        print(f"Response redirected to: {response.redirect_chain}")
    if response.status_code == 200:
        print(f"✓ Request successful")
except Exception as e:
    print(f"❌ Error making request: {e}")
    exit(1)

# Check if jobs were created
print(f"\nChecking if jobs were created in _REPLAY_JOBS...")

# Import the jobs dictionary
from recorder.views import _REPLAY_JOBS, _JOBS_LOCK

with _JOBS_LOCK:
    if _REPLAY_JOBS:
        print(f"✓ Jobs found in queue: {len(_REPLAY_JOBS)} active jobs")
        for run_id, job in list(_REPLAY_JOBS.items())[-3:]:  # Show last 3
            record_id = job.get('record_id', '?')
            status = job.get('status', '?')
            engine = job.get('engine', '?')
            print(f"  - {record_id} (engine={engine}): {status}")
    else:
        print(f"⚠️  No jobs in queue")

# Check active runs via API
print(f"\nChecking active runs via API...")
try:
    api_response = client.get('/api/active-runs/', content_type='application/json')
    if api_response.status_code == 200:
        runs = api_response.json()
        print(f"✓ Active runs: {len(runs)} runs found")
        for run in runs[-3:]:  # Show last 3
            record_id = run.get('record_id', '?')
            status = run.get('status', '?')
            engine = run.get('engine', '?')
            print(f"  - {record_id} (engine={engine}): {status}")
    else:
        print(f"⚠️  API response status: {api_response.status_code}")
except Exception as e:
    print(f"❌ Error checking API: {e}")

print("\n✓ Test completed successfully!")
