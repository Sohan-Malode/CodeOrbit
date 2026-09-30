from shop.core.accounts import all_accounts


def record(message):
    return (message, all_accounts)
