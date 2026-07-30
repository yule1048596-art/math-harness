class MathHarnessError(Exception):
    """Base exception for domain errors."""


class WorkspaceNotFound(MathHarnessError):
    pass


class RecordNotFound(MathHarnessError):
    pass


class UnsafeExpression(MathHarnessError):
    pass
