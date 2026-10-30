# Prints MU3LAB_ROLES_OK once Paperless has Mu3Lab's two roles (run by `manage.py shell`).
"""Mu3Lab's roles as Paperless groups, which every sign-in syncs from Authentik's groups claim.

Members file, tag, search and share their own documents and mail; administrators may also manage
people, settings and workflows. Nobody is a superuser, so nobody sees another person's private
documents: a document is seen by its owner and whoever it is shared with.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission

ADMIN, MEMBER = "mu3lab-operators", "mu3lab-users"
# Workflows act on every incoming document, whoever it belongs to.
ADMIN_ONLY = (
    "workflow",
    "workflowtrigger",
    "workflowaction",
    "workflowactionemail",
    "workflowactionwebhook",
    "workflowrun",
)

member = Group.objects.get_or_create(name=MEMBER)[0]
member.permissions.set(
    Permission.objects.filter(content_type__app_label__in=("documents", "paperless_mail")).exclude(
        content_type__model__in=ADMIN_ONLY
    )
    # Sharing a document lists the people and groups to share it with.
    | Permission.objects.filter(content_type__app_label="auth", codename__in=("view_user", "view_group"))
)
admin = Group.objects.get_or_create(name=ADMIN)[0]
admin.permissions.set(Permission.objects.all())

# The first administrator was created a superuser; from now on the administrators' group stands in,
# and sign-ins keep it in step with Authentik.
for user in get_user_model().objects.filter(is_superuser=True):
    user.groups.add(admin, member)
    user.is_superuser = False
    user.is_staff = True
    user.save(update_fields=["is_superuser", "is_staff"])
print("MU3LAB_ROLES_OK")
