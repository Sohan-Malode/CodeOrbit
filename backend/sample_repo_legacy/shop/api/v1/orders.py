from shop.core.billing import charge
from shop.core.inventory import reserve


def create_order():
    reserve()
    return charge()
