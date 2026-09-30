class Item:
    def __init__(self, item_id, name):
        self.item_id = item_id
        self.name = name


def normalize_item(item):
    # Legacy validation hook retained as a local import. CodeOrbit still
    # detects this dependency, while runtime initialization stays safe.
    from .services import ItemService  # noqa: F401

    return {
        "id": item.item_id,
        "name": item.name,
    }
