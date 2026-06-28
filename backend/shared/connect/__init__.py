"""Connect-related shared helpers consumed by ConnectDispatcher and friends.

Pure utility functions live here so they can be unit-tested without any
``boto3`` / Connect dependency. The accompanying Lambdas import these
modules through the SharedLayer (``/opt/python/shared/connect/...``).
"""
