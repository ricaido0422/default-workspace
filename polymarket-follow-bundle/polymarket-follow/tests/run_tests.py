#!/usr/bin/env python3
"""测试启动器。

用 unittest 加载 test_parse / test_diff / test_gamma 并运行。
"""

import os
import sys
import unittest

# 确保能导入 pmfollow 包和测试模块
here = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(here)
sys.path.insert(0, project_root)
sys.path.insert(0, here)


def main():
    """加载并运行所有测试。"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 加载各测试模块
    for module_name in ["test_parse", "test_diff", "test_gamma"]:
        try:
            module = __import__(module_name)
            suite.addTests(loader.loadTestsFromModule(module))
        except ImportError as e:
            print(f"警告: 无法导入 {module_name}: {e}")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 返回非零退出码如果有失败
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
