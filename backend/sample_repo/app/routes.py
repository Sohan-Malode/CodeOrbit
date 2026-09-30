from .services import ItemService


def register_routes(app):
    service = ItemService()

    @app.get("/")
    def health():
        return {"status": "ok", "service": "inventory"}

    @app.get("/items")
    def get_items():
        return {"items": service.list_items()}

    @app.get("/items/<int:item_id>")
    def get_item(item_id):
        item = service.get_item(item_id)
        if item is None:
            return {"error": "Item not found"}, 404
        return {"item": item}
