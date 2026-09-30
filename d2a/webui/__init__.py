# -*- coding: utf-8 -*-
"""Web 面板：静态资源 + 本地 HTTP API。

对外暴露 :func:`serve`、:func:`serve_from_env`（容器入口用）与 :class:`ApiState`。
"""

from .server import ApiState, make_handler, serve, serve_from_env

__all__ = ["ApiState", "make_handler", "serve", "serve_from_env"]
