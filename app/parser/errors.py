class ParserError(Exception):
    pass


class InvalidUsernameError(ParserError):
    pass


class ChannelNotFoundError(ParserError):
    pass


class FetchError(ParserError):
    pass
