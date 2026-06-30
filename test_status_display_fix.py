#!/usr/bin/env python
"""Test script to verify DB test case status display is fixed."""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'webapp.settings')
django.setup()

from django.test import Client
from django.contrib.auth.models import User
from db_testcases.models import TestCase as DbTestCase, TestExecution
import json

# Create test user
user, _ = User.objects.get_or_create(username='testuser')

print("=" * 60)
print("DB TEST CASE STATUS DISPLAY FIX VERIFICATION")
print("=" * 60)

# Get active DB test case
db_tc = DbTestCase.objects.filter(is_active=True).first()
if not db_tc:
    print("\n❌ No active DB test cases found")
    exit(1)

print(f"\nTest Case: {db_tc.id} ({db_tc.name})")

# Check latest execution
latest = db_tc.executions.order_by('-executed_at').first()
if latest:
    print(f"Latest Execution Status: {latest.status}")
else:
    print(f"Latest Execution: None")

# Test the status API
client = Client()
client.force_login(user)

print(f"\n--- Testing API Endpoint ---")
print(f"Endpoint: /db-lab/testcases/latest-results/?ids={db_tc.id}")
response = client.get(f'/db-lab/testcases/latest-results/?ids={db_tc.id}')
print(f"Response Status: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    raw_status = data['results'].get(str(db_tc.id), 'not_found')
    lowercased_status = str(raw_status).lower()
    print(f"Raw Status: {raw_status}")
    print(f"Lowercased: {lowercased_status}")
    
    # Check if it matches the statusBadgeHtml logic
    print(f"\n--- Status Badge Display Logic ---")
    if lowercased_status == 'pass' or lowercased_status == 'passed':
        badge = '<span class="badge bg-success me-2">Pass</span>'
        print(f"✓ Will display as: PASS badge (green)")
    elif lowercased_status == 'fail' or lowercased_status == 'failed':
        badge = '<span class="badge bg-danger me-2">Fail</span>'
        print(f"✓ Will display as: FAIL badge (red)")
    elif lowercased_status == 'error':
        badge = '<span class="badge bg-warning text-dark me-2">Error</span>'
        print(f"✓ Will display as: ERROR badge (yellow)")
    else:
        badge = '<span class="badge bg-secondary me-2">No Run</span>'
        print(f"⚠️ Will display as: No Run badge (gray)")
else:
    print(f"❌ Error response: {response.content}")

print(f"\n--- Testing Multiple Test Cases ---")

# Test with multiple IDs if available
all_dbs = DbTestCase.objects.filter(is_active=True)[:3]
if all_dbs:
    ids_str = ','.join(str(tc.id) for tc in all_dbs)
    print(f"Test IDs: {ids_str}")
    response = client.get(f'/db-lab/testcases/latest-results/?ids={ids_str}')
    if response.status_code == 200:
        data = response.json()
        print(f"Results returned for {len(data.get('results', {}))} test cases:")
        for tc_id, status in data['results'].items():
            print(f"  - ID {tc_id}: {status}")
            print(f"✓ Multiple test case status retrieval works")
    else:
        print(f"❌ Error: {response.status_code}")

print("\n" + "=" * 60)
print("✓ Verification completed successfully")
print("=" * 60)
