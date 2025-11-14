from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase
from django.utils import timezone

from allauth.socialaccount.models import SocialAccount, SocialApp, SocialLogin, SocialToken
from allauth.socialaccount.signals import social_account_added, social_account_updated

from tracker import models


class SocialAuthSignalTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.app = SocialApp.objects.create(
            provider="google",
            name="Google",
            client_id="test-client-id",
            secret="test-client-secret",
        )
        site = Site.objects.get_current()
        self.app.sites.add(site)

    def _social_login(self, user, refresh_token="refresh-token", access_token="access-token"):
        account = SocialAccount.objects.create(user=user, provider="google", uid=f"uid-{user.pk}")
        token = SocialToken(
            app=self.app,
            token=access_token,
            token_secret=refresh_token,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        token.account = account
        return SocialLogin(user=user, account=account, token=token)

    def test_google_social_login_stores_gmail_credentials(self):
        user = self.user_model.objects.create_user(
            email="user@example.com",
            username="user1",
            password=None,
        )
        sociallogin = self._social_login(user)

        social_account_added.send(sender=SocialLogin, request=None, sociallogin=sociallogin)

        account = models.EmailAccount.objects.get(
            email_address=user.email, provider=models.EmailAccount.Provider.GMAIL
        )
        self.assertEqual(account.token_json["refresh_token"], "refresh-token")
        self.assertEqual(account.token_json["token"], "access-token")

    def test_google_social_login_updates_existing_credentials(self):
        user = self.user_model.objects.create_user(
            email="user2@example.com",
            username="user2",
            password=None,
        )
        sociallogin = self._social_login(user, refresh_token="first-refresh", access_token="first-access")

        social_account_added.send(sender=SocialLogin, request=None, sociallogin=sociallogin)

        sociallogin.token.token = "new-access"
        sociallogin.token.token_secret = "new-refresh"
        social_account_updated.send(sender=SocialLogin, request=None, sociallogin=sociallogin)

        account = models.EmailAccount.objects.get(
            email_address=user.email, provider=models.EmailAccount.Provider.GMAIL
        )
        self.assertEqual(account.token_json["refresh_token"], "new-refresh")
        self.assertEqual(account.token_json["token"], "new-access")
