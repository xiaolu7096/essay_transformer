"""论文易读化转换器核心包（essay_transformer）。

核心库优先（宪法原则 I）：除 ``cli.py`` 外，本包任何模块禁止 import
argparse 或任何 UI/Web 框架；业务模块只接收 LLMClient / MinerUClient
Protocol 实例，不直接构造真实客户端。
"""

__version__ = "0.1.0"
