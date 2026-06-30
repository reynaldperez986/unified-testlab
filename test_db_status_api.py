#!/usr/bin/env python
"""Test script to verify DB test case status retrieval."""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'webapp.settings')
django.setup()

from django.test import Client
from django.contrib.auth.models import User
from db_testcases.models import TestCase as DbTestCase, TestExecution

# Create test user
user, _ = User.objects.get_or_create(username='testuser')

# Get a DB test case
tc = DbTestCase.objects.filter(is_active=True).first()
if not tc:
    print("❌ No active DB test cases found")
    exit(1)

print(f"Test Case: {tc.id} ({tc.name})")

# Check latest execution
latest = tc.executions.order_by('-executed_at').first()
if latest:
    print(f"Latest Execution Status: {latest.status}")
    print(f"Executed At: {latest.executed_at}")
else:
    print(f"Latest Execution: None")

# Test the API endpoint
client = Client()
client.force_login(user)

print(f"\nTesting API endpoint: /db-lab/testcases/latest-results/?ids={tc.id}")
response = client.get(f'/db-lab/testcases/latest-results/?ids={tc.id}')
print(f"Response Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    print(f"Response Data: {data}")
    if 'results' in data:
        result_status = data['results'].get(str(tc.id), 'not_found')
        print(f"✓ Test case {tc.id} status: {result_status}")
else:
    print(f"❌ Error response: {response.content}")

print("\n✓ Test completed")
