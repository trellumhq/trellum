"""One module per command group.

Split out of a 1,335-line cli.py that held roughly twenty-five _cmd_*
handlers -- guide, checks, validate, setup, data, query, serve, doctor and
the whole review client -- in a single namespace.
"""
