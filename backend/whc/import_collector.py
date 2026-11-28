"""Run as one independent service; leases permit safe restarts or multiple collectors."""
import logging
import time

from . import create_app
from .models import db
from .pending_imports import collect_one


def main():
    app = create_app()
    while True:
        with app.app_context():
            try:
                worked = collect_one()
            except Exception:
                db.session.rollback()
                logging.exception('Import collection failed; retained job will retry after its lease expires')
                worked = False
            finally:
                db.session.remove()
        if not worked:
            time.sleep(1)


if __name__ == '__main__':
    main()
