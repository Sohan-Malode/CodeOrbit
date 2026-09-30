import unittest

from shop.core.billing import charge
from shop.legacy.old_export import export


class BillingTests(unittest.TestCase):
    def test_charge(self):
        self.assertEqual(charge(), "$1.00")
        self.assertEqual(export(), "csv")


if __name__ == "__main__":
    unittest.main()
