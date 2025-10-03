from django.contrib.auth.models import User
from django.test import TestCase
from my_application.models import Item


class ItemTestCase(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="test_user", password="")
        Item.objects.create(name="unittest", owner=user)

    def test_item(self):
        item = Item.objects.get(name="unittest")
        self.assertEqual(item.name, "unittest")

    def test_owner(self):
        item = Item.objects.get(name="unittest")
        owner = item.owner
        self.assertEqual(owner.username, "test_user")

    def test_fail(self):
        self.assertEqual(1, 2)