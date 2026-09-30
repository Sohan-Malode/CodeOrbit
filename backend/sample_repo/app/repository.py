from .models import Item


class ItemRepository:
    def __init__(self):
        self._items = [
            Item(1, "Keyboard"),
            Item(2, "Mouse"),
            Item(3, "Monitor"),
        ]

    def all(self):
        return list(self._items)

    def get(self, item_id):
        return next(
            (item for item in self._items if item.item_id == item_id),
            None,
        )
