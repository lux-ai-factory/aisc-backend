"""Custom allauth adapter that syncs OIDC roles to Django Groups."""

import logging

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth.models import Group

logger = logging.getLogger(__name__)


class OIDCAccountAdapter(DefaultSocialAccountAdapter):
    """Sync realm roles from the OIDC token to Django Groups on every login."""

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        extra_data = sociallogin.account.extra_data
        if "admin" in set(extra_data.get("roles", [])):
            user.is_staff = True
        return user

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        self._sync_roles(user, sociallogin)
        return user

    def on_pre_social_login(self, request, sociallogin):
        super().on_pre_social_login(request, sociallogin)
        if sociallogin.user and sociallogin.user.pk:
            self._sync_roles(sociallogin.user, sociallogin)

    @staticmethod
    def _sync_roles(user, sociallogin):
        """Map OIDC realm roles to Django Groups and update is_staff."""
        extra_data = sociallogin.account.extra_data
        oidc_roles = set(extra_data.get("roles", []))

        # Filter out Keycloak internal composite roles
        skip = {"offline_access", "uma_authorization", "default-roles-a4s"}
        oidc_roles -= skip

        # Ensure matching Django Groups exist and assign them
        groups = []
        for role_name in oidc_roles:
            group, _ = Group.objects.get_or_create(name=role_name)
            groups.append(group)

        user.groups.set(groups)
        user.is_staff = "admin" in oidc_roles
        user.save(update_fields=["is_staff"])

        logger.info("Synced roles %s for user %s", oidc_roles, user.username)
