from shop.core import accounts


def list_customers():
    return accounts.all_accounts()
