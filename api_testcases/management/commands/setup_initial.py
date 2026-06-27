from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from api_testcases.models import UserProfile


class Command(BaseCommand):
    help = 'Create initial admin user and setup'

    def handle(self, *args, **options):
        if not User.objects.filter(username='admin').exists():
            user = User.objects.create_superuser(
                username='admin',
                email='admin@example.com',
                password='admin',
                first_name='System',
                last_name='Administrator',
            )
            UserProfile.objects.create(user=user, role='admin')
            self.stdout.write(self.style.SUCCESS('Admin user created (admin/admin)'))
        else:
            admin_user = User.objects.get(username='admin')
            if not admin_user.check_password('admin'):
                admin_user.set_password('admin')
                admin_user.save(update_fields=['password'])
            self.stdout.write(self.style.WARNING('Admin user already exists (password synced to admin)'))

        # Ensure all users have profiles
        for user in User.objects.all():
            UserProfile.objects.get_or_create(user=user, defaults={'role': 'tester'})

        self.stdout.write(self.style.SUCCESS('Setup complete'))

