#!/usr/bin/env python
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'webapp.settings')
django.setup()

from django.test import Client
from django.contrib.auth.models import User

user, _ = User.objects.get_or_create(username='admin', defaults={'is_staff': True, 'is_superuser': True})
client = Client()
client.force_login(user)

response = client.post('/sessions/bulk-replay/', {
    'api_testcase_ids': ['6'],
    'db_testcase_ids': ['1'],
    'headless': 'on',
}, follow=False)
print('Redirect:', response.status_code, response.get('Location', ''))

time.sleep(0.1)

from recorder.views import _REPLAY_JOBS, _JOBS_LOCK
with _JOBS_LOCK:
    print('Total jobs in _REPLAY_JOBS:', len(_REPLAY_JOBS))
    for run_id, job in list(_REPLAY_JOBS.items()):
        rid = job.get('record_id', '?')
        folder = job.get('folder_name', '?')
        status = job.get('status', '?')
        engine = job.get('engine', '?')
        print(f'  record_id={rid}  folder={folder}  status={status}  engine={engine}')

# Also check the active-runs API
r2 = client.get('/api/active-runs/')
import json
data = json.loads(r2.content)
print('\nactive-runs API returns', len(data.get('runs', [])), 'jobs:')
for run in data.get('runs', []):
    print(f"  record_id={run['record_id']}  folder={run['folder_name']}  status={run['status']}")
