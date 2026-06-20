"""Ingestion package — scheduled jobs that pull from external sources into Postgres.

Ingestion is a separate concern from the API: nothing here is imported by request
handlers. External sources (FBref / football-data.co.uk / …) are touched ONLY here.
"""
