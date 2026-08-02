class MathHarnessError(Exception):
    """Base exception for domain errors."""


class WorkspaceNotFound(MathHarnessError):
    pass


class RecordNotFound(MathHarnessError):
    pass


class InvalidKnowledgeState(MathHarnessError):
    """The requested knowledge transition would cross a trust boundary."""


class InvalidPortableData(MathHarnessError):
    """An import or backup payload is malformed, unsafe, or unsupported."""


class UnsafeExpression(MathHarnessError):
    pass
