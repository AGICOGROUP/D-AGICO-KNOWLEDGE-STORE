class KBError(Exception):
    def __init__(self, code, message, status=409, **details):
        self.status = status
        self.payload = {"error": {"code": code, "message": message, **details}}


def not_found():
    raise KBError("NOT_FOUND", "资料不存在或无权访问。", 404)
