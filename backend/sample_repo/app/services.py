from .models import normalize_item
from .repository import ItemRepository


class ItemService:
    def __init__(self):
        self.repository = ItemRepository()

    def list_items(self):
        return [
            normalize_item(item)
            for item in self.repository.all()
        ]

    def get_item(self, item_id):
        item = self.repository.get(item_id)
        return normalize_item(item) if item else None
