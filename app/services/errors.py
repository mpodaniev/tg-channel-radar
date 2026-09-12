class ServiceError(Exception):
    pass


class InvalidChannelUsernameError(ServiceError):
    pass


class ChannelNotFoundInDbError(ServiceError):
    pass


class ChannelAlreadyExistsError(ServiceError):
    pass


class InvalidPeriodError(ServiceError):
    pass


class PostNotFoundError(ServiceError):
    pass


class AiUnavailableError(ServiceError):
    pass
