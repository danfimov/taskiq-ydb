class BaseTaskiqYdbError(Exception):
    """Base error for all possible exception in the lib."""


class DatabaseConnectionError(BaseTaskiqYdbError):
    """Error if cannot connect to PostgreSQL."""


class ResultIsMissingError(BaseTaskiqYdbError):
    """Error if cannot retrieve result from PostgreSQL."""
