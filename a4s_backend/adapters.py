"""Custom allauth adapter that syncs OIDC roles to Django Groups.

Authentication flow:
    The frontend submits a hidden form with the login provider reference to allauth,
    which handles the communication with Keycloak following the OIDC protocol.
    Keycloak acts as the identity provider and verifies whether the user exists
    after they enter their username/email and password. If valid, Keycloak returns
    a signed token which is then decoded and validated by the backend to start
    a new session.

    This adapter extends that flow by syncing the roles carried in the OIDC token
    (e.g. admin, user) to Django Groups on every login.
"""

import logging

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth.models import Group

logger = logging.getLogger(__name__)


class OIDCAccountAdapter(DefaultSocialAccountAdapter):
    """Sync realm roles from the OIDC token to Django Groups on every login."""

    @staticmethod
    def _extract_roles(sociallogin):
        """Extract roles from extra_data — handles nested userinfo/id_token."""
        extra_data = sociallogin.account.extra_data
        # Roles can be at top level, in userinfo, or in id_token
        roles = extra_data.get("roles")
        if not roles:
            roles = extra_data.get("userinfo", {}).get("roles")
        if not roles:
            roles = extra_data.get("id_token", {}).get("roles")
        return set(roles or [])

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        if "admin" in self._extract_roles(sociallogin):
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
        # Roles can be at top level, in userinfo, or in id_token
        roles = extra_data.get("roles")
        if not roles:
            roles = extra_data.get("userinfo", {}).get("roles")
        if not roles:
            roles = extra_data.get("id_token", {}).get("roles")
        oidc_roles = set(roles or [])

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
