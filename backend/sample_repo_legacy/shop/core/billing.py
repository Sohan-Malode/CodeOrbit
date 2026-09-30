from shop.core.inventory import reserve
from shop.utils.money import fmt


def charge():
    reserve()
    return fmt(1)
