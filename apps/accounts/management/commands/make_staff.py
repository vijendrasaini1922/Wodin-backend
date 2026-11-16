"""Create (or promote) a staff account that is claimed by signing in with Google.

    python manage.py make_staff you@gmail.com --role admin --name "Your Name"

Use this once to create the first admin; afterwards add the team from the Staff page.
"""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from apps.accounts.google_auth import provision_staff
from apps.accounts.models import Role


class Command(BaseCommand):
    help = "Create or update a staff account for a Google email (claimed on first Google sign-in)."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--role", default="admin", choices=[r for r in Role.values if r != Role.CUSTOMER])
        parser.add_argument("--name", default="")

    def handle(self, *args, email, role, name, **options):
        try:
            validate_email(email)
        except ValidationError as exc:
            raise CommandError("Invalid email address.") from exc
        user, created = provision_staff(email, role, name)
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} {role}: {user.email}. They become this account on their first Google sign-in."))
