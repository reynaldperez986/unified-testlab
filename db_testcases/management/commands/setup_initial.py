from django.contrib.auth.models import Group, Permission, User
from django.core.management.base import BaseCommand

from db_testcases.models import Theme


class Command(BaseCommand):
    help = "Create default roles and users for DB TestLab"

    def handle(self, *args, **options):
        Theme.objects.get_or_create(
            name="Default",
            defaults={
                "is_default": True,
                "primary_color": "#0f766e",
                "accent_color": "#f59e0b",
                "background_color": "#f3f7f5",
                "surface_color": "#ffffff",
                "text_color": "#122322",
                "border_color": "#d7e2df",
                "sidebar_start_color": "#0b2d2a",
                "sidebar_end_color": "#133734",
            },
        )

        admin_group, _ = Group.objects.get_or_create(name="Admin")
        tester_group, _ = Group.objects.get_or_create(name="Tester")
        viewer_group, _ = Group.objects.get_or_create(name="Viewer")

        all_permissions = Permission.objects.all()
        admin_group.permissions.set(all_permissions)

        tester_codenames = [
            "view_databaseconnection",
            "add_databaseconnection",
            "change_databaseconnection",
            "view_testcase",
            "add_testcase",
            "change_testcase",
            "view_testexecution",
            "add_testexecution",
        ]
        tester_permissions = Permission.objects.filter(codename__in=tester_codenames)
        tester_group.permissions.set(tester_permissions)

        viewer_codenames = [
            "view_databaseconnection",
            "view_testcase",
            "view_testexecution",
        ]
        viewer_permissions = Permission.objects.filter(codename__in=viewer_codenames)
        viewer_group.permissions.set(viewer_permissions)

        admin_user, created = User.objects.get_or_create(
            username="admin",
            defaults={"is_staff": True, "is_superuser": True},
        )
        if created:
            admin_user.set_password("admin")
            admin_user.save()
        else:
            changed = False
            if not admin_user.is_staff:
                admin_user.is_staff = True
                changed = True
            if not admin_user.is_superuser:
                admin_user.is_superuser = True
                changed = True
            if not admin_user.check_password("admin"):
                admin_user.set_password("admin")
                changed = True
            if changed:
                admin_user.save()

        tester_user, created = User.objects.get_or_create(
            username="tester",
            defaults={"is_staff": True},
        )
        if created:
            tester_user.set_password("tester123")
            tester_user.save()
        tester_user.groups.add(tester_group)

        viewer_user, created = User.objects.get_or_create(
            username="viewer",
            defaults={"is_staff": False},
        )
        if created:
            viewer_user.set_password("viewer123")
            viewer_user.save()
        viewer_user.groups.add(viewer_group)

        self.stdout.write(self.style.SUCCESS("Roles and default users ensured."))
        self.stdout.write("Admin: admin / admin")
        self.stdout.write("Tester: tester / tester123")
        self.stdout.write("Viewer: viewer / viewer123")

