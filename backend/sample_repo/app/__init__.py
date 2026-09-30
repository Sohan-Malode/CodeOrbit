from flask import Flask

from .routes import register_routes


app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
register_routes(app)
