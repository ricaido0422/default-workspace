# -*- coding: utf-8 -*-
"""临时：清理两次 E2E 测试注入的重复 0xdir / 0xtest 行。"""
import sqlite3

con = sqlite3.connect('data/trader.db')
n1 = con.execute("DELETE FROM trades WHERE wallet = ?", ("0xdir",)).rowcount
n2 = con.execute("DELETE FROM trades WHERE wallet = ?", ("0xtest",)).rowcount
con.commit()
total = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
con.close()
print('deleted 0xdir:', n1, '| 0xtest:', n2, '| remaining:', total)
