class AppError(Exception):
    """Base class for application-level errors."""


class AzureApiError(AppError):
    """Raised when an Azure Resource Manager or Monitor call fails."""
