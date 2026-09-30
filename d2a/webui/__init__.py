# -*- coding: utf-8 -*-
"""Web 面板：静态资源 + 本地 HTTP API。

对外只暴露 :func:`serve` 与 :class:`ApiState`。
"""

from .server import ApiState, make_handler, serve

__all__ = ["ApiState", "make_handler", "serve"]
