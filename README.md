# Crypto Futures / T-Bank Trading Bot

Репозиторий торгового бота для **T-Bank Invest API** (фьючерсы MOEX) с risk engine, анализом рыночного фона и long/short сигналами.

## Возможности

- Автоматический анализ фьючерсов MOEX (Si, RTS и др.)
- Сигналы **Long** и **Short** на основе EMA, ADX, RSI, ATR
- **Market context**: тренд, волатильность, сессия MOEX, confidence score
- **Risk engine**: лимит на сделку, дневной kill-switch, R:R ≥ 1.5, sizing по лотам
- Режимы: `paper` (без ордеров) и `live` (реальные заявки)
- **Sandbox** T-Bank по умолчанию
- Уведомления в Telegram (опционально)
- **Telegram-дашборд**: старт/стоп, статус, настройки, скан

## Telegram Dashboard

Бот запускается с панелью управления в Telegram:

| Кнопка / команда | Действие |
|------------------|----------|
| ▶️ Старт / `/start_bot` | Запуск торгового цикла |
| ⏹ Стоп / `/stop_bot` | Остановка цикла |
| 🔍 Скан / `/scan` | Один цикл анализа сейчас |
| 📊 Статус / `/status` | Состояние, risk, портфель |
| 📈 Позиции / `/positions` | Открытые фьючерсы |
| ⚙️ Настройки | Inline-кнопки risk/conf/interval/mode |

Текстовые команды: `/mode paper`, `/risk 1.0`, `/confidence 65`, `/interval 300`, `/tickers SiH5,RIH5`, `/sl 1.5`, `/tp 3.0`

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=ваш_chat_id
TELEGRAM_ADMIN_IDS=123456789   # опционально, через запятую
```

## Быстрый старт

### 1. Токен T-Bank

1. Откройте [T-Bank Invest Open API](https://www.tbank.ru/invest/open-api/)
2. Создайте токен с правами на торговлю (или read-only для paper)
3. Для тестов включите **Sandbox** в личном кабинете

### 2. Установка

```bash
pip install -r requirements.txt
cp .env.example .env
# Заполните TBANK_TOKEN в .env
```

### 3. Запуск (один цикл, paper)

```bash
export PYTHONPATH=src
python src/tbank_bot/main.py --once
```

### 4. Запуск с Telegram-дашбордом

```bash
# TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID в .env
PYTHONPATH=src python src/tbank_bot/main.py
```

В Telegram: `/start` → ▶️ Старт / ⏹ Стоп / ⚙️ Настройки

### 5. Live (осторожно, после sandbox!)

```bash
TRADING_MODE=live TBANK_SANDBOX=true PYTHONPATH=src python src/tbank_bot/main.py
```

## Переменные окружения

| Переменная | Описание |
|------------|----------|
| `TBANK_TOKEN` | API-токен T-Bank Invest |
| `TBANK_SANDBOX` | `true` = песочница |
| `TRADING_MODE` | `paper` или `live` |
| `TBANK_FUTURES_TICKERS` | Тикеры через запятую (пусто = Si + RTS) |
| `MAX_RISK_PER_TRADE_PCT` | Риск на сделку, % от портфеля |
| `MAX_DAILY_LOSS_PCT` | Kill-switch при дневном убытке |
| `TELEGRAM_BOT_TOKEN` | Опционально |
| `TELEGRAM_CHAT_ID` | Опционально |

## Архитектура

```
src/tbank_bot/
├── broker/tbank.py      # T-Bank Invest API
├── strategy/            # Сигналы long/short + market context
├── risk/engine.py       # Risk management
├── engine/trader.py     # Торговый цикл
├── dashboard/         # Telegram UI (старт/стоп/настройки)
├── state/controller.py # Runtime state
└── main.py
src/tinkoff/             # Vendored T-Bank gRPC SDK
```

## Стратегия

1. Загрузка часовых свечей (45 дней)
2. Индикаторы: EMA20/50, ADX, RSI, ATR
3. **Long**: восходящий тренд, ADX≥20, RSI 35–68
4. **Short**: нисходящий тренд, ADX≥20, RSI 32–65
5. Фильтр высокой волатильности (ATR percentile > 80%)
6. SL = 1.5×ATR, TP = 3×ATR (R:R ≈ 2)
7. Risk engine проверяет confidence, лимиты, margin

## Документация

- [T-Bank Invest API](https://tinkoff.github.io/investAPI/)
- [docs/TRADING_BOT_PROPOSAL.md](docs/TRADING_BOT_PROPOSAL.md)

## Disclaimer

Торговля фьючерсами связана с высоким риском. Бот не гарантирует прибыль. Начинайте с sandbox и режима `paper`.
