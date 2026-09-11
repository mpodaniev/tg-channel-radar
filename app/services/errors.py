class ServiceError(Exception):
    pass


class InvalidChannelUsernameError(ServiceError):
    pass


class ChannelNotFoundInDbError(ServiceError):
    pass


class InvalidPeriodError(ServiceError):
    pass
