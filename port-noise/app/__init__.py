"""Flask 应用工厂。"""
import os

from flask import Flask

from . import db, routes


def create_app(database=None):
    app = Flask(__name__)
    app.config["DATABASE"] = database or os.environ.get(
        "DATABASE", os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                 "data", "port_noise.sqlite3"))
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024  # 一季 CSV 上限 256MB
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
    db.init_app(app)
    app.register_blueprint(routes.bp)
    return app
