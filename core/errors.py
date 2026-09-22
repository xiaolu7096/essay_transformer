"""核心层异常：只保留 3 类，够用即可。"""


class ETError(Exception):
    """基础异常"""
    pass


class InputError(ETError):
    """输入有问题（文件不存在/格式不对/链接不支持），用户修了就能好"""
    pass


class ServiceError(ETError):
    """外部服务问题（API 挂了/网络错/超时），重试可能好"""
    pass
