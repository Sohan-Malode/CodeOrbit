from shop.utils.audit import record


def notify(message):
    record(message)
