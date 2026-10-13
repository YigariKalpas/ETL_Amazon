r"""Windows 沙箱兼容层（DSH workspace-write）。

背景 / 根因
------------
本机 DSH 沙箱为子进程附加了受限访问控制：工作区目录的 ACL 上会继承一条
授予沙箱受限 SID (S-1-4-*) 的写权限。CPython 在 Windows 上按 mode 参数创建
目录时（tempfile.mkdtemp 内部使用 os.mkdir(path, 0o700)）会生成一个**不继承**
的收紧 DACL，其中不含该受限 SID，导致连创建者进程都无法再写入或 scandir 该
目录。表现为：
    $ python -m venv .venv            # ensurepip 失败
    PermissionError: [Errno 13] ... tmpXXXX\pip-*.whl

行为
----
在 win32 上把 os.mkdir 的 mode 统一放宽为 0o777（继承父目录 ACL），使临时目录
继承沙箱可写 ACE。仅影响本解释器进程，不修改系统策略。

注意：这是环境适配层，不是业务代码。它必须随 venv 一起重建，因此源文件保存在
仓库 environment/ 下，由 scripts/bootstrap_env.ps1 复制进 .venv/Lib/site-packages。
"""

import os
import sys

if sys.platform == "win32":
    _real_mkdir = os.mkdir

    def _sandbox_safe_mkdir(path, mode=0o777, *args, **kwargs):
        # 忽略调用方传入的 mode，改用继承式默认权限，保证沙箱受限 SID 可访问
        return _real_mkdir(path, 0o777, *args, **kwargs)

    _sandbox_safe_mkdir.__doc__ = (
        "patched by sitecustomize: mode ignored on win32 to keep sandbox ACL inheritable"
    )
    os.mkdir = _sandbox_safe_mkdir
