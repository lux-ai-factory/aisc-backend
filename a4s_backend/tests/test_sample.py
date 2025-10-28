from django.test import TestCase

class SampleTestCase(TestCase):

    def test_pass(self):
        self.assertTrue(True)

    #def test_fail(self):
    #    self.assertTrue(False)