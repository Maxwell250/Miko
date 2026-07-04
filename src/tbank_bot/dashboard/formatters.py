from __future__ import annotations

from typing import TYPE_CHECKING, List

from tbank_bot.telegram_html import escape_html

if TYPE_CHECKING:
    from tbank_bot.broker.tbank import AccountSnapshot
    from tbank_bot.config import Settings
    from tbank_bot.state.controller import BotController


def format_status(
    controller: "BotController",
    base_settings: "Settings",
    snapshot: "AccountSnapshot | None" = None,
) -> str:
    eff = controller.get_effective_settings(base_settings)
    state = "🟢 Работает" if controller.is_running() else "🔴 Остановлен"
    lines = [
        "<b>📊 T-Bank Futures Bot</b>",
        f"Состояние: {state}",
        f"Режим: <code>{eff.trading_mode.value}</code>",
        f"Sandbox: <code>{eff.tbank_sandbox}</code>",
        f"Циклов: {controller.cycles_total}",
    ]
    if controller.last_cycle_at:
        lines.append(f"Последний цикл: {controller.last_cycle_at.strftime('%H:%M:%S UTC')}")
    if controller.last_error:
        lines.append(f"⚠️ Ошибка: {escape_html(controller.last_error[:200])}")

    lines.extend(
        [
            "",
            "<b>Risk</b>",
            f"• Риск/сделка: {eff.max_risk_per_trade_pct}%",
            f"• Kill-switch: {eff.max_daily_loss_pct}%",
            f"• Max позиций: {eff.max_open_positions}",
            f"• Min confidence: {eff.min_signal_confidence}",
            f"• SL/TP ATR: {eff.default_stop_atr_mult} / {eff.default_tp_atr_mult}",
            f"• Интервал: {eff.loop_interval_sec}с",
        ]
    )

    tickers = eff.futures_ticker_list or ["Si + RTS (авто)"]
    lines.append(f"• Тикеры: {', '.join(tickers)}")

    if snapshot:
        lines.extend(
            [
                "",
                "<b>Портфель</b>",
                f"• Equity: {snapshot.total_amount:,.2f} ₽",
                f"• Доступно: {snapshot.available:,.2f} ₽",
                f"• Позиций: {len(snapshot.positions)}",
            ]
        )
    return "\n".join(lines)


def format_positions(snapshot: "AccountSnapshot") -> str:
    if not snapshot.positions:
        return "📈 Открытых фьючерсных позиций нет."
    lines = ["<b>📈 Позиции</b>"]
    for p in snapshot.positions:
        emoji = "🟢" if p.direction == "long" else "🔴"
        lines.append(
            f"{emoji} <b>{p.ticker}</b> {p.direction.upper()} "
            f"{p.lots} лот · avg {p.avg_price:.2f} · PnL {p.expected_yield:+.2f} ₽"
        )
    return "\n".join(lines)


def format_scan_results(results: List[dict]) -> str:
    if not results:
        return "🔍 Скан завершён — нет данных."
    lines = [
        "<b>🔍 Результаты скана</b>",
        "<i>Только анализ рынка, ордера не выставляются.</i>",
        "",
    ]
    for r in results:
        ticker = escape_html(r.get("ticker", "?"))
        action = escape_html(r.get("action", "?"))
        direction = escape_html(r.get("direction", "-"))
        conf = escape_html(r.get("confidence", "-"))
        reason = escape_html(r.get("reason", r.get("risk_reason", "")))
        market = r.get("market_open")
        market_tag = ""
        if market is False:
            market_tag = " 🔒биржа закрыта"
        elif market is True:
            market_tag = " 🟢биржа открыта"
        lines.append(f"\n<b>{ticker}</b>{market_tag}")
        lines.append(
            f"  {action} · {direction} · conf {conf} · score {escape_html(r.get('rank_score', '-'))}"
        )
        if r.get("regime"):
            lines.append(
                f"  режим={escape_html(r.get('regime'))} · сессия={escape_html(r.get('session', '-'))}"
            )
        if r.get("trend"):
            lines.append(
                f"  trend={escape_html(r.get('trend'))} ADX={escape_html(r.get('adx'))} "
                f"RSI={escape_html(r.get('rsi'))} vol={escape_html(r.get('volatility'))}"
            )
        if r.get("margin"):
            margin_txt = f"{r['margin']:,.0f} ₽"
            lines.append(f"  ГО ~{escape_html(margin_txt)}")
        if r.get("structure_summary"):
            lines.append(f"  📐 {escape_html(r['structure_summary'][:140])}")
        if r.get("entry"):
            lines.append(
                f"  Entry {escape_html(r['entry'])} SL {escape_html(r.get('sl'))} "
                f"TP {escape_html(r.get('tp'))}"
            )
        lines.append(f"  {reason[:200]}")
    return "\n".join(lines)


def format_help() -> str:
    return """<b>❓ Команды дашборда</b>

<b>Управление</b>
▶️ Старт / /start_bot — запуск цикла
⏹ Стоп / /stop_bot — остановка
🔍 Скан / /scan — один цикл сейчас
📊 Статус / /status — состояние бота
📈 Позиции / /positions — открытые позиции

<b>Настройки (текст)</b>
/mode paper|live
/risk 1.0 — риск % на сделку
/confidence 65 — min confidence
/interval 300 — интервал сек
/tickers SiH5,RIH5 — фьючерсы
/sl 1.5 — множитель SL ATR
/tp 3.0 — множитель TP ATR

⚙️ Настройки — кнопки для быстрой корректировки"""
