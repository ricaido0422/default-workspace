"""SQLite 状态存储模块。

使用 aiosqlite 提供异步 CRUD 接口。
表: wallets, trades, signals, executions, pipelines, config。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from backend.models.schemas import (
    ExecutionRecord,
    FollowSignal,
    StrategyPipeline,
    TradeEvent,
    VirtualPosition,
    WalletWatch,
)

logger = logging.getLogger(__name__)


class StateStore:
    """异步 SQLite 状态存储。"""

    def __init__(self, db_path: str = "data/trader.db") -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    # ── 生命周期 ──────────────────────────────────────────

    async def connect(self) -> None:
        """连接数据库并初始化表结构。"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA temp_store=MEMORY")
        await self._init_tables()
        async with self._db.execute('PRAGMA table_info(executions)') as cur:
            columns = {row['name'] for row in await cur.fetchall()}
        if 'account_id' not in columns:
            await self._db.execute("ALTER TABLE executions ADD COLUMN account_id TEXT NOT NULL DEFAULT 'legacy'")
            await self._db.commit()

    async def close(self) -> None:
        """关闭数据库连接。"""
        if self._db:
            await self._db.close()
            self._db = None

    async def _init_tables(self) -> None:
        """创建所有表（IF NOT EXISTS）。"""
        assert self._db is not None
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS wallets (
                address      TEXT PRIMARY KEY,
                alias        TEXT DEFAULT '',
                enabled      INTEGER DEFAULT 1,
                added_at     TEXT,
                last_checked TEXT,
                trade_count  INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS trades (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet        TEXT NOT NULL,
                condition_id  TEXT NOT NULL,
                token_id      TEXT NOT NULL,
                market_slug   TEXT DEFAULT '',
                market_title  TEXT DEFAULT '',
                side          TEXT NOT NULL,
                price         REAL NOT NULL,
                size          REAL NOT NULL,
                timestamp     TEXT NOT NULL,
                tx_hash       TEXT DEFAULT '',
                asset_id      TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS signals (
                signal_id    TEXT PRIMARY KEY,
                pipeline_id  TEXT NOT NULL,
                source_trade TEXT NOT NULL,
                market       TEXT NOT NULL,
                side         TEXT NOT NULL,
                price        REAL NOT NULL,
                size         REAL NOT NULL,
                strategy_chain TEXT DEFAULT '[]',
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS executions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id   TEXT NOT NULL,
                status      TEXT NOT NULL,
                order_id    TEXT DEFAULT '',
                fill_price  REAL,
                fill_size   REAL,
                pnl         REAL,
                mode        TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                error       TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS pipelines (
                id             TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                strategies     TEXT DEFAULT '[]',
                enabled        INTEGER DEFAULT 1,
                target_wallets TEXT DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS notifications_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                channel    TEXT NOT NULL,
                status     TEXT NOT NULL,
                error      TEXT DEFAULT '',
                payload    TEXT DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_trades_wallet ON trades(wallet);
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_executions_signal ON executions(signal_id);
            CREATE TABLE IF NOT EXISTS virtual_positions (
                wallet        TEXT NOT NULL,
                token_id      TEXT NOT NULL,
                condition_id  TEXT DEFAULT '',
                market_title  TEXT DEFAULT '',
                market_slug   TEXT DEFAULT '',
                net_qty       REAL DEFAULT 0,
                avg_entry     REAL DEFAULT 0,
                realized_pnl  REAL DEFAULT 0,
                opened_at     TEXT,
                updated_at    TEXT,
                PRIMARY KEY (wallet, token_id)
            );

            CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications_log(created_at);
            CREATE TABLE IF NOT EXISTS desk_queue (
                signal_id TEXT PRIMARY KEY, mode TEXT NOT NULL, status TEXT NOT NULL,
                signal TEXT NOT NULL, reason TEXT DEFAULT '', created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_desk_queue_created ON desk_queue(created_at DESC);
            CREATE TABLE IF NOT EXISTS desk_fills (
                signal_id TEXT PRIMARY KEY, mode TEXT NOT NULL, signal TEXT NOT NULL,
                price REAL NOT NULL, size REAL NOT NULL, fee_bps REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account_profiles (id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS order_intents (id TEXT PRIMARY KEY,account_id TEXT NOT NULL,intent TEXT NOT NULL,signal TEXT NOT NULL,submission TEXT,state TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',attempts INTEGER NOT NULL DEFAULT 0,next_check REAL NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS wallet_jobs (id TEXT PRIMARY KEY,account_id TEXT NOT NULL,intent_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',expires_at TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS account_snapshots (account_id TEXT PRIMARY KEY,payload TEXT NOT NULL,observed_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS account_events (id INTEGER PRIMARY KEY AUTOINCREMENT,account_id TEXT NOT NULL,intent_id TEXT NOT NULL,state TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_intents_pending ON order_intents(state,next_check);
            CREATE TABLE IF NOT EXISTS realtime_trades (
                trade_key TEXT PRIMARY KEY, payload TEXT NOT NULL,
                received_at TEXT NOT NULL, source TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_realtime_received ON realtime_trades(received_at);
            CREATE TABLE IF NOT EXISTS market_resolutions (
                condition_id TEXT PRIMARY KEY, payload TEXT NOT NULL, checked_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_settlements (
                settlement_id TEXT PRIMARY KEY, token_id TEXT UNIQUE NOT NULL,
                condition_id TEXT NOT NULL, price REAL NOT NULL, quantity REAL NOT NULL,
                source TEXT NOT NULL, payload TEXT NOT NULL, signal TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS paper_settlement_fill
            AFTER INSERT ON paper_settlements BEGIN
                INSERT INTO desk_fills(signal_id,mode,signal,price,size,fee_bps,created_at)
                VALUES(NEW.settlement_id,'PAPER',NEW.signal,NEW.price,NEW.quantity,0,NEW.created_at);
            END;
            """
        )
        await self._db.commit()

    # ── 钱包 CRUD ─────────────────────────────────────────

    async def add_wallet(self, wallet: WalletWatch) -> WalletWatch:
        """添加监控钱包。"""
        assert self._db is not None
        await self._db.execute(
            """INSERT OR REPLACE INTO wallets
               (address, alias, enabled, added_at, last_checked, trade_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                wallet.address,
                wallet.alias,
                int(wallet.enabled),
                wallet.added_at.isoformat(),
                wallet.last_checked.isoformat() if wallet.last_checked else None,
                wallet.trade_count,
            ),
        )
        await self._db.commit()
        return wallet

    async def get_wallets(self) -> list[WalletWatch]:
        """获取所有监控钱包。"""
        assert self._db is not None
        cursor = await self._db.execute("SELECT * FROM wallets ORDER BY added_at")
        rows = await cursor.fetchall()
        return [self._row_to_wallet(row) for row in rows]

    async def get_wallet(self, address: str) -> WalletWatch | None:
        """获取单个钱包。"""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM wallets WHERE address = ?", (address,)
        )
        row = await cursor.fetchone()
        return self._row_to_wallet(row) if row else None

    async def delete_wallet(self, address: str) -> bool:
        """删除钱包。"""
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM wallets WHERE address = ?", (address,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def toggle_wallet(self, address: str) -> WalletWatch | None:
        """切换钱包启用状态。"""
        assert self._db is not None
        cursor = await self._db.execute(
            "UPDATE wallets SET enabled = 1 - enabled WHERE address = ?",
            (address,),
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            return None
        return await self.get_wallet(address)

    async def update_wallet_status(
        self, address: str, last_checked: datetime, trade_count: int
    ) -> None:
        """更新钱包检查状态。"""
        assert self._db is not None
        await self._db.execute(
            """UPDATE wallets SET last_checked = ?, trade_count = ?
               WHERE address = ?""",
            (last_checked.isoformat(), trade_count, address),
        )
        await self._db.commit()

    # ── 交易 CRUD ─────────────────────────────────────────

    async def save_trade(self, trade: TradeEvent) -> None:
        """保存交易事件。"""
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO trades
               (wallet, condition_id, token_id, market_slug, market_title,
                side, price, size, timestamp, tx_hash, asset_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade.wallet,
                trade.condition_id,
                trade.token_id,
                trade.market_slug,
                trade.market_title,
                trade.side.value,
                trade.price,
                trade.size,
                trade.timestamp.isoformat(),
                trade.tx_hash,
                trade.asset_id,
            ),
        )
        await self._db.commit()

    async def has_trade(self, trade: TradeEvent) -> bool:
        """跨重启去重，不依赖监控器的内存集合。"""
        assert self._db is not None
        if trade.tx_hash:
            sql = "SELECT 1 FROM trades WHERE wallet = ? AND token_id = ? AND tx_hash = ? LIMIT 1"
            args = (trade.wallet, trade.token_id, trade.tx_hash)
        else:
            sql = "SELECT 1 FROM trades WHERE wallet = ? AND token_id = ? AND timestamp = ? LIMIT 1"
            args = (trade.wallet, trade.token_id, trade.timestamp.isoformat())
        async with self._db.execute(sql, args) as cursor:
            return await cursor.fetchone() is not None

    async def get_trades(
        self, wallet: str | None = None, limit: int = 50
    ) -> list[TradeEvent]:
        """获取交易列表。"""
        assert self._db is not None
        if wallet:
            cursor = await self._db.execute(
                "SELECT * FROM trades WHERE wallet = ? ORDER BY timestamp DESC LIMIT ?",
                (wallet, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
            )
        rows = await cursor.fetchall()
        return [self._row_to_trade(row) for row in rows]

    # ── 信号 CRUD ─────────────────────────────────────────

    async def save_signal(self, signal: FollowSignal) -> None:
        """保存跟单信号。"""
        assert self._db is not None
        await self._db.execute(
            """INSERT OR REPLACE INTO signals
               (signal_id, pipeline_id, source_trade, market, side, price, size,
                strategy_chain, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal.signal_id,
                signal.pipeline_id,
                signal.source_trade.model_dump_json(),
                signal.market.model_dump_json(),
                signal.side.value,
                signal.price,
                signal.size,
                json.dumps(signal.strategy_chain),
                signal.created_at.isoformat(),
            ),
        )
        await self._db.commit()

    async def get_signals(self, limit: int = 50) -> list[FollowSignal]:
        """获取最近的跟单信号列表。"""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_signal(row) for row in rows]

    # ── 执行记录 CRUD ─────────────────────────────────────
    async def get_execution(self, signal_id: str) -> ExecutionRecord | None:
        assert self._db is not None
        async with self._db.execute("SELECT * FROM executions WHERE signal_id = ? ORDER BY id DESC LIMIT 1", (signal_id,)) as cursor:
            row = await cursor.fetchone()
        return self._row_to_execution(row) if row else None

    async def update_execution(self, record: ExecutionRecord) -> None:
        assert self._db is not None
        await self._db.execute(
            "UPDATE executions SET status=?, order_id=?, fill_price=?, fill_size=?, error=? WHERE signal_id=? AND mode='LIVE' AND account_id=?",
            (record.status.value, record.order_id, record.fill_price, record.fill_size, record.error, record.signal_id, record.account_id),
        )
        await self._db.commit()

    async def save_execution(self, record: ExecutionRecord) -> None:
        """保存执行记录。"""
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO executions
               (signal_id, status, order_id, fill_price, fill_size, pnl, mode,
                timestamp, error, account_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.signal_id,
                record.status.value,
                record.order_id,
                record.fill_price,
                record.fill_size,
                record.pnl,
                record.mode.value,
                record.timestamp.isoformat(),
                record.error,
                record.account_id,
            ),
        )
        await self._db.commit()

    async def get_executions(self, limit: int = 50) -> list[ExecutionRecord]:
        """获取执行记录列表。"""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM executions ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_execution(row) for row in rows]

    # ── 流水线 CRUD ───────────────────────────────────────

    async def save_pipeline(self, pipeline: StrategyPipeline) -> StrategyPipeline:
        """保存策略流水线。"""
        assert self._db is not None
        await self._db.execute(
            """INSERT OR REPLACE INTO pipelines
               (id, name, strategies, enabled, target_wallets)
               VALUES (?, ?, ?, ?, ?)""",
            (
                pipeline.id,
                pipeline.name,
                json.dumps([s.model_dump() for s in pipeline.strategies]),
                int(pipeline.enabled),
                json.dumps(pipeline.target_wallets),
            ),
        )
        await self._db.commit()
        return pipeline

    async def get_pipelines(self) -> list[StrategyPipeline]:
        """获取所有流水线。"""
        assert self._db is not None
        cursor = await self._db.execute("SELECT * FROM pipelines ORDER BY name")
        rows = await cursor.fetchall()
        return [self._row_to_pipeline(row) for row in rows]

    async def get_pipeline(self, pipeline_id: str) -> StrategyPipeline | None:
        """获取单个流水线。"""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)
        )
        row = await cursor.fetchone()
        return self._row_to_pipeline(row) if row else None

    async def delete_pipeline(self, pipeline_id: str) -> bool:
        """删除流水线。"""
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM pipelines WHERE id = ?", (pipeline_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    # ── 配置键值存储 ──────────────────────────────────────

    async def set_config(self, key: str, value: Any) -> None:
        """设置配置键值。"""
        assert self._db is not None
        await self._db.execute(
            """INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)""",
            (key, json.dumps(value)),
        )
        await self._db.commit()

    async def get_config(self, key: str) -> Any:
        """获取配置键值。"""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return json.loads(row["value"]) if row else None

    # ── 通知日志 CRUD ──────────────────────────────────────

    async def log_notification(
        self,
        event_type: str,
        channel: str,
        status: str,
        error: str = "",
        payload: dict[str, Any] | None = None,
    ) -> int:
        """记录一条通知发送日志。

        Args:
            event_type: 事件类型
            channel: 通知渠道 (telegram / email)
            status: 发送状态 (success / failed)
            error: 错误信息（成功时为空）
            payload: 附加数据（标题 / 正文 / 结构化数据）

        Returns:
            新记录的自增 id（失败返回 0）
        """
        assert self._db is not None
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            payload_json = json.dumps(payload or {}, ensure_ascii=False, default=str)
        except Exception:
            payload_json = "{}"
        try:
            cursor = await self._db.execute(
                """INSERT INTO notifications_log
                   (created_at, event_type, channel, status, error, payload)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    created_at,
                    event_type,
                    channel,
                    status,
                    error or "",
                    payload_json,
                ),
            )
            await self._db.commit()
            return int(cursor.lastrowid or 0)
        except Exception:
            logger.exception("写通知日志失败")
            return 0

    async def get_notification_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取最近的通知发送记录。

        Args:
            limit: 返回条数上限

        Returns:
            记录列表（按时间倒序），每项包含
            ``id / created_at / event_type / channel / status / error / payload``
        """
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM notifications_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_notification_log(row) for row in rows]

    @staticmethod
    def _row_to_notification_log(row: aiosqlite.Row) -> dict[str, Any]:
        """DB 行 → 通知日志字典。"""
        try:
            payload = json.loads(row["payload"]) if row["payload"] else {}
        except Exception:
            payload = {}
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "event_type": row["event_type"],
            "channel": row["channel"],
            "status": row["status"],
            "error": row["error"] or "",
            "payload": payload,
        }

    # ── 辅助方法 ──────────────────────────────────────────

    @staticmethod
    def _row_to_wallet(row: aiosqlite.Row) -> WalletWatch:
        return WalletWatch(
            address=row["address"],
            alias=row["alias"],
            enabled=bool(row["enabled"]),
            added_at=datetime.fromisoformat(row["added_at"]),
            last_checked=datetime.fromisoformat(row["last_checked"])
            if row["last_checked"]
            else None,
            trade_count=row["trade_count"],
        )

    @staticmethod
    def _row_to_trade(row: aiosqlite.Row) -> TradeEvent:
        from backend.models.schemas import TradeSide

        return TradeEvent(
            wallet=row["wallet"],
            condition_id=row["condition_id"],
            token_id=row["token_id"],
            market_slug=row["market_slug"],
            market_title=row["market_title"],
            side=TradeSide(row["side"]),
            price=row["price"],
            size=row["size"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            tx_hash=row["tx_hash"],
            asset_id=row["asset_id"],
        )

    @staticmethod
    def _row_to_signal(row: aiosqlite.Row) -> FollowSignal:
        """DB 行 → FollowSignal。"""
        from backend.models.schemas import MarketRef, TradeSide

        try:
            source_trade = TradeEvent.model_validate_json(row["source_trade"])
        except Exception:
            source_trade = TradeEvent(
                wallet="",
                condition_id="",
                token_id="",
                side=TradeSide.BUY,
                price=0.0,
                size=0.0,
            )
        try:
            market = MarketRef.model_validate_json(row["market"])
        except Exception:
            market = MarketRef(condition_id="", token_id="")
        try:
            chain = json.loads(row["strategy_chain"]) if row["strategy_chain"] else []
        except Exception:
            chain = []
        return FollowSignal(
            source_trade=source_trade,
            pipeline_id=row["pipeline_id"],
            market=market,
            side=TradeSide(row["side"]),
            price=row["price"],
            size=row["size"],
            strategy_chain=chain,
            created_at=datetime.fromisoformat(row["created_at"]),
            signal_id=row["signal_id"],
        )

    @staticmethod
    def _row_to_execution(row: aiosqlite.Row) -> ExecutionRecord:
        from backend.models.schemas import ExecutionMode, ExecutionStatus

        return ExecutionRecord(
            signal_id=row["signal_id"],
            status=ExecutionStatus(row["status"]),
            order_id=row["order_id"],
            fill_price=row["fill_price"],
            fill_size=row["fill_size"],
            pnl=row["pnl"],
            mode=ExecutionMode(row["mode"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            error=row["error"],
            account_id=row['account_id'],
        )

    # ── 虚拟持仓（模拟资金 + 实盘盯市结算） ──────────────────

    async def upsert_virtual_position(self, pos: VirtualPosition) -> None:
        """写入/更新一条虚拟持仓。"""
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO virtual_positions
               (wallet, token_id, condition_id, market_title, market_slug,
                net_qty, avg_entry, realized_pnl, opened_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(wallet, token_id) DO UPDATE SET
                 condition_id=excluded.condition_id,
                 market_title=excluded.market_title,
                 market_slug=excluded.market_slug,
                 net_qty=excluded.net_qty,
                 avg_entry=excluded.avg_entry,
                 realized_pnl=excluded.realized_pnl,
                 updated_at=excluded.updated_at""",
            (
                pos.wallet,
                pos.token_id,
                pos.condition_id,
                pos.market_title,
                pos.market_slug,
                pos.net_qty,
                pos.avg_entry,
                pos.realized_pnl,
                pos.opened_at.isoformat(),
                pos.updated_at.isoformat(),
            ),
        )
        await self._db.commit()

    async def get_virtual_positions(
        self, wallet: str | None = None, open_only: bool = False
    ) -> list[VirtualPosition]:
        """获取虚拟持仓列表（可按钱包过滤，可只看未平仓）。"""
        assert self._db is not None
        sql = "SELECT * FROM virtual_positions"
        clauses: list[str] = []
        params: list[Any] = []
        if wallet:
            clauses.append("wallet = ?")
            params.append(wallet)
        if open_only:
            clauses.append("ABS(net_qty) > 1e-9")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC"
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [self._row_to_virtual_position(row) for row in rows]

    async def get_virtual_position(
        self, wallet: str, token_id: str
    ) -> VirtualPosition | None:
        """获取单个虚拟持仓。"""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM virtual_positions WHERE wallet = ? AND token_id = ?",
            (wallet, token_id),
        )
        row = await cursor.fetchone()
        return self._row_to_virtual_position(row) if row else None

    async def clear_virtual_positions(self, wallet: str | None = None) -> int:
        """清空虚拟持仓（用于重置模拟账户），返回删除行数。"""
        assert self._db is not None
        if wallet:
            cursor = await self._db.execute(
                "DELETE FROM virtual_positions WHERE wallet = ?", (wallet,)
            )
        else:
            cursor = await self._db.execute("DELETE FROM virtual_positions")
        await self._db.commit()
        return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

    @staticmethod
    def _row_to_pipeline(row: aiosqlite.Row) -> StrategyPipeline:
        from backend.models.schemas import StrategyConfig

        strategies_data = json.loads(row["strategies"]) if row["strategies"] else []
        strategies = [StrategyConfig(**s) for s in strategies_data]
        target_wallets = json.loads(row["target_wallets"]) if row["target_wallets"] else []
        return StrategyPipeline(
            id=row["id"],
            name=row["name"],
            strategies=strategies,
            enabled=bool(row["enabled"]),
            target_wallets=target_wallets,
        )

    @staticmethod
    def _row_to_virtual_position(row: aiosqlite.Row) -> VirtualPosition:
        return VirtualPosition(
            wallet=row["wallet"],
            token_id=row["token_id"],
            condition_id=row["condition_id"] or "",
            market_title=row["market_title"] or "",
            market_slug=row["market_slug"] or "",
            net_qty=float(row["net_qty"] or 0.0),
            avg_entry=float(row["avg_entry"] or 0.0),
            realized_pnl=float(row["realized_pnl"] or 0.0),
            opened_at=(
                datetime.fromisoformat(row["opened_at"])
                if row["opened_at"]
                else datetime.now(timezone.utc)
            ),
            updated_at=(
                datetime.fromisoformat(row["updated_at"])
                if row["updated_at"]
                else datetime.now(timezone.utc)
            ),
        )


# ── 工具函数 ────────────────────────────────────────────────

def generate_id(prefix: str = "") -> str:
    """生成唯一 id。"""
    return f"{prefix}{uuid.uuid4().hex[:12]}"
