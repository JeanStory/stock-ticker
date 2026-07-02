"""PyInstaller 打包入口。

PyInstaller 不能直接打 ``python -m`` 形式的包，需要一个显式脚本作为入口。
开发/源码运行仍用 ``python -m stock_glance``；打包发行走这个文件。
"""

from stock_glance.__main__ import main

if __name__ == "__main__":
    main()
