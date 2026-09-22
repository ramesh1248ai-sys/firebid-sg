"""The Procrastinate app. The worker opens it; the API only queues jobs on its own connection."""

from procrastinate import App, PsycopgConnector

from firebid.settings import get_settings

app = App(
    connector=PsycopgConnector(conninfo=get_settings().database_url),
    import_paths=["firebid.jobs.tasks"],
)
