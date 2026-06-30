#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'webapp.settings')
django.setup()

from db_testcases.models import TestCase as DbTestCase
from api_testcases.models import TestCase as ApiTestCase

db_count = DbTestCase.objects.filter(is_active=True).count()
api_count = ApiTestCase.objects.filter(is_active=True).count()

print(f"Active DB Test Cases: {db_count}")
print(f"Active API Test Cases: {api_count}")

if db_count > 0:
    db_tc = DbTestCase.objects.filter(is_active=True).first()
    print(f"  First DB Test Case: ID={db_tc.id}, Name={db_tc.name}")

if api_count > 0:
    api_tc = ApiTestCase.objects.filter(is_active=True).first()
    print(f"  First API Test Case: ID={api_tc.id}, Name={api_tc.name}")
