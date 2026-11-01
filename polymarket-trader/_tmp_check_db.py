# -*- coding: utf-8 -*-
"""临时：检查注入的测试交易行。"""
import sqlite3

con = sqlite3.connect('data/trader.db')
rows = con.execute(
    "SELECT side, price, size, wallet FROM trades WHERE wallet = ?", ("0xdir",)
).fetchall()
print('0xdir rows:', len(rows))
for r in rows:
    print('  ', r)
total = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
print('total trades in DB:', total)
con.close()
