"""Procrastinate worker entrypoint: ``python -m library.worker``.

Runs the worker programmatically against the job app defined in
``library.jobs`` (this is the command of the ``worker`` service in
docker-compose).
"""

import logging

from library.jobs import job_app


def main() -> None:
    """Open the job app and run the worker until interrupted."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    with job_app.open():
        job_app.run_worker()


if __name__ == "__main__":
    main()
