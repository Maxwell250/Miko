# Анализ: Telegram-бот для торговли крипто-фьючерсами

## 1. Контекст и цель

Telegram Wallet (The Open Platform) интегрировал perpetual futures через DEX **Lighter**: до 50× плечо, 50+ инструментов (BTC, ETH, TON, commodities, tokenized stocks/ETF), минимальный вход от $1. Торговля доступна внутри Telegram, но **публичного API для Wallet пока нет** — исполнение сделок идёт через кастодиальный кошелёк TOP.

**Вывод:** бот может работать в двух режимах:

| Режим | Описание | Сложность | Контроль |
|-------|----------|-----------|----------|
| **A. Сигнальный** | Бот анализирует рынок, пользователь исполняет в Wallet вручную | Низкая | Полный у пользователя |
| **B. Авто через Lighter API** | Бот торгует через API Lighter (тот же движок, что у Wallet) | Высокая | Некастодиальный кошелёк пользователя |
| **C. Гибрид** | Сигнал + deep link / кнопка «Открыть в Wallet» + опционально API | Средняя | Гибкий |

Рекомендация для MVP: **режим C** — сигналы + risk-калькулятор + paper trading, затем подключение Lighter API для автоисполнения.

---

## 2. Архитектура системы

```
┌─────────────────────────────────────────────────────────────┐
│                    TELEGRAM BOT (UI Layer)                   │
│  /start /balance /signal /positions /settings /news       │
│  Inline-кнопки: Long/Short, TP/SL, размер позиции         │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                   ORCHESTRATOR (Python/Node)                  │
│  Маршрутизация команд · авторизация · rate limits            │
└───┬─────────────┬──────────────┬──────────────┬─────────────┘
    │             │              │              │
    ▼             ▼              ▼              ▼
┌────────┐  ┌──────────┐  ┌───────────┐  ┌──────────────┐
│ Market │  │ Strategy │  │   Risk    │  │  Execution   │
│ Engine │  │  Engine  │  │  Engine   │  │   Engine     │
└───┬────┘  └────┬─────┘  └─────┬─────┘  └──────┬───────┘
    │            │              │               │
    └────────────┴──────────────┴───────────────┘
                           │
              ┌────────────▼────────────┐
              │   PostgreSQL + Redis    │
              │   TimescaleDB (свечи)   │
              └─────────────────────────┘
```

### Сервисы

1. **Market Engine** — сбор и нормализация рыночных данных
2. **Strategy Engine** — генерация сигналов (техника + sentiment + macro)
3. **Risk Engine** — sizing, SL/TP, лимиты просадки, kill-switch
4. **Execution Engine** — ордера через Lighter SDK / уведомления для ручного входа
5. **News & Sentiment** — RSS, CryptoPanic, Fear & Greed, funding rates

---

## 3. База данных (всё необходимое для торговли)

### 3.1 Справочники (static / редко меняется)

```sql
-- Инструменты
instruments (symbol, base, quote, market_id, min_notional, tick_size, max_leverage)

-- Комиссии (критично для PnL!)
fee_schedule (
  exchange,          -- 'lighter', 'binance' (для сравнения)
  tier,              -- retail / maker / taker
  maker_fee_bps,     -- basis points
  taker_fee_bps,
  funding_interval_h,
  updated_at
)

-- Календарь событий
economic_calendar (datetime, event, impact, country, forecast, previous)
```

### 3.2 Рыночные данные (high-frequency)

```sql
-- OHLCV (TimescaleDB hypertable)
candles (time, symbol, interval, open, high, low, close, volume)

-- Order book snapshots
orderbook_snapshots (time, symbol, bids_json, asks_json, spread_bps)

-- Funding & open interest
funding_rates (time, symbol, rate, predicted_rate)
open_interest (time, symbol, oi_usd, oi_change_24h)

-- Индекс страха/жадности, доминация BTC
market_regime (time, fear_greed, btc_dominance, total_market_cap, vix_proxy)
```

### 3.3 Новостной и sentiment-слой

```sql
news_items (time, source, title, url, symbols[], sentiment_score, impact)
social_metrics (time, symbol, mentions_1h, sentiment_avg, whale_alerts)
```

Источники:
- **CryptoPanic API** — агрегатор новостей с тегами
- **CoinGecko / CoinMarketCap** — macro metrics
- **Alternative.me** — Fear & Greed Index
- **RSS**: CoinDesk, The Block, официальные блоги бирж
- **Telegram-каналы** (whitelist) — парсинг через MTProto (осторожно с ToS)

### 3.4 Пользователи и сделки

```sql
users (telegram_id, lighter_account_index, risk_profile, max_daily_loss_pct)
positions (user_id, symbol, side, entry, size, leverage, sl, tp, status)
trades (position_id, fill_price, fee_usd, pnl_usd, executed_at)
signals (strategy_id, symbol, direction, confidence, reasoning_json, created_at)
bot_performance (date, strategy, win_rate, avg_rr, sharpe, max_dd)
```

### 3.5 Redis (hot cache)

- Текущие цены (TTL 1–5 сек)
- Активные позиции пользователя
- Очередь сигналов
- Rate limit counters

---

## 4. Учёт комиссий (ключ к «выгодности»)

Без точного учёта комиссий бот **всегда** переоценит доходность.

### Формула реального PnL на фьючерсах

```
net_pnl = gross_pnl
        - entry_fee
        - exit_fee
        - funding_payments (каждые 8ч на большинстве perp)
        - slippage_estimate
        - gas (для on-chain Lighter, обычно минимален на L2)
```

### Lighter (актуально на 2026)

- **Retail: 0% maker/taker** на spot/perp (основное преимущество vs CEX)
- Funding rate — платится long/short в зависимости от imbalance
- API colocation: AWS Tokyo `ap-northeast-1a` для минимальной latency

### Рекомендации

1. Хранить fee_schedule с версионированием — биржи меняют тарифы
2. В каждом сигнале показывать **breakeven move** с учётом funding
3. Не входить, если expected move < 2× (fee + slippage + funding buffer)
4. Логировать фактические vs ожидаемые комиссии для калибровки

---

## 5. Стратегии с положительным мат. ожиданием

> ⚠️ Ни одна стратегия не гарантирует прибыль. Ниже — подходы с доказанной robustness при правильном risk management.

### 5.1 Trend Following + Regime Filter (основная)

**Логика:**
- Тренд: EMA 20/50 crossover + ADX > 25
- Regime filter: торговать long только когда Fear & Greed > 30 и funding не экстремально положительный
- Entry: pullback к EMA20 в направлении тренда
- Exit: trailing stop 1.5× ATR, TP 3× ATR (R:R ≈ 1:2)

**Почему работает на perp:** тренды в крипте длиннее, чем на spot; плечо увеличивает ROI, но risk engine ограничивает size.

### 5.2 Funding Rate Arbitrage / Mean Reversion

**Логика:**
- При funding > 0.05%/8h и RSI > 70 → сигнал SHORT (перегретый long bias)
- При funding < -0.03% → сигнал LONG
- Размер: обратно пропорционален |funding|

**Edge:** рынок периодически переплачивает за направленный bias.

### 5.3 News Momentum (event-driven)

**Логика:**
- NLP sentiment score > 0.7 + volume spike > 2× avg → momentum entry
- Time stop: 4 часа (новостной импульс затухает)
- Hard SL: 1% от entry

**Фильтр:** игнорировать low-impact news (рейтинг impact в БД).

### 5.4 Multi-Timeframe Confluence

```
1H  — определяет направление (структура HH/HL или LH/LL)
15M — точка входа (RSI divergence, volume profile POC)
5M  — timing (order flow imbalance из orderbook)
```

Сигнал только при совпадении ≥ 2 таймфреймов.

---

## 6. Risk Engine (главный «защитник» депозита)

```yaml
defaults:
  max_risk_per_trade: 1.0%      # от equity
  max_daily_loss: 3.0%
  max_open_positions: 3
  max_leverage: 10              # даже если биржа даёт 50×
  max_correlation_exposure: 2   # не более 2 позиций в одном направлении на BTC-correlated

kill_switch:
  trigger: daily_loss >= 3% OR 3 consecutive losses
  action: close_all + pause 24h + notify user

position_sizing:
  formula: size = (equity × risk_pct) / (entry - stop_loss)
  min_rr: 1.5                     # не открывать сделки с R:R < 1.5
```

---

## 7. Telegram UX (предложения)

### Команды MVP

| Команда | Функция |
|---------|---------|
| `/start` | Онбординг, выбор risk profile |
| `/market` | Обзор: BTC/ETH цена, F&G, funding, топ новости |
| `/signal` | Последний сигнал с кнопками Long/Short/Skip |
| `/calc` | Калькулятор: размер, SL, TP, комиссии, breakeven |
| `/positions` | Открытые позиции + unrealized PnL |
| `/stats` | Win rate, PnL за неделю/месяц |
| `/settings` | Risk %, max leverage, уведомления |
| `/news` | Лента с sentiment по watchlist |

### Формат сигнала

```
🟢 LONG BTC-PERP
━━━━━━━━━━━━━━━━━━
Entry:    $67,450 – $67,520
SL:       $66,800 (-1.0%)
TP1/TP2:  $68,200 / $69,000
Size:     $500 (10× → $5,000 notional)
R:R:      1 : 2.1

📊 Контекст:
• EMA20>EMA50, ADX 32
• Funding: +0.008%/8h (нейтральный)
• F&G: 55 (Greed)
• Новости: нейтральные

💰 Комиссии (Lighter):
• Entry/Exit: $0.00
• Est. funding 24h: -$0.40
• Breakeven: +0.12%

[✅ Подтвердить] [📊 Калькулятор] [❌ Skip]
```

---

## 8. Технический стек (рекомендация)

| Компонент | Технология |
|-----------|------------|
| Bot framework | Python 3.12 + `aiogram 3.x` |
| Execution | `lighter-python` SDK (официальный) |
| Market data | Lighter WS + Binance (backup/reference) |
| Database | PostgreSQL 16 + TimescaleDB |
| Cache/Queue | Redis + Celery / ARQ |
| Backtesting | `vectorbt` или custom на pandas |
| Monitoring | Grafana + Prometheus, алерты в Telegram |
| Deploy | Docker Compose → VPS (Tokyo region) |

---

## 9. Roadmap

### Phase 0 — Подготовка (текущая)
- [x] Очистка репозитория от Miko
- [ ] Архитектурный документ (этот файл)
- [ ] Выбор режима A/B/C

### Phase 1 — Data Layer (2–3 недели)
- [ ] Схема БД + миграции
- [ ] Ingestion: свечи, funding, F&G, news
- [ ] Fee calculator module

### Phase 2 — Signal Bot (2–3 недели)
- [ ] Telegram bot skeleton
- [ ] 1 стратегия (Trend + Regime)
- [ ] Paper trading tracker
- [ ] Backtest на 6+ месяцев данных

### Phase 3 — Execution (2–4 недели)
- [ ] Lighter API integration
- [ ] Risk engine + kill switch
- [ ] Live paper → micro-live ($10–50)

### Phase 4 — Intelligence
- [ ] News NLP sentiment
- [ ] Multi-strategy portfolio
- [ ] Performance dashboard

---

## 10. Риски и ограничения

1. **Нет публичного Wallet API** — автоисполнение только через Lighter напрямую (отдельный аккаунт от Wallet)
2. **Geo restrictions** — US/UK пользователи не имеют доступа к Wallet Perps
3. **Leverage** — 50× легко уничтожает депозит; жёсткий cap 5–10× в боте
4. **Overfitting** — обязательный walk-forward backtest, out-of-sample validation
5. **Security** — API keys только в env/vault, IP whitelist, никогда в коде (в старом Miko был Discord token в репо — **немедленно отозвать!**)

---

## 11. Метрики успеха

| Метрика | Цель (после 3 мес paper) |
|---------|--------------------------|
| Win Rate | > 45% при R:R ≥ 1.5 |
| Profit Factor | > 1.3 |
| Max Drawdown | < 15% |
| Sharpe Ratio | > 1.0 |
| Avg trades/day | 1–3 (не overtrading) |

---

## 12. Следующий шаг для реализации

1. Подтвердить режим работы (сигналы / авто / гибрид)
2. Создать `docker-compose.yml` + схему БД
3. Написать MVP Telegram-бота с `/market` и `/calc`
4. Подключить Lighter public API для market data
5. Бэктест первой стратегии
