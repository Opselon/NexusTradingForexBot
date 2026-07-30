# Nexus Scalp Engine (NSE) — Production Quantitative Scalping Infrastructure

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.2%2B-red.svg)](https://pytorch.org/)
[![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5-gold.svg)](https://www.mql5.com/)
[![Architecture](https://img.shields.io/badge/Architecture-Hexagonal-purple.svg)]()
[![License](https://img.shields.io/badge/License-Proprietary-green.svg)]()

**Nexus Scalp Engine (NSE)** is an enterprise-grade, high-frequency quantitative scalp trading engine engineered specifically for **XAUUSD (Gold)** and major currency pairs. Built natively with **Python 3.11+**, **PyTorch deep learning**, and direct **C++ IPC bindings** to MetaTrader 5, the system provides zero-data-leakage feature pipelines, ICT/Ichimoku multi-confluence signal evaluation, dynamic margin-based position sizing, and automated thread-replied Telegram telemetry.

---

## 🏛️ System Architecture

The engine implements a **Hexagonal / Ports-and-Adapters Monolith Architecture** prioritizing thread safety, ultra-low execution latency, and complete execution-platform isolation.

```text
┌───────────────────────────────────────────────────────────────────────────────────┐
│                          LINUX / WINDOWS CORE RUNTIME                             │
│                                                                                   │
│  ┌───────────────────────┐   ┌───────────────────────┐   ┌─────────────────────┐  │
│  │  Incremental Feature  │   │  Hierarchical PyTorch │   │ Dynamic Risk Engine │  │
│  │ Engine (13 Dimensions)│───│ ScalpNet v2 (TCN+Attn)│───│(Margin/Leverage Cap)│  │
│  └───────────┬───────────┘   └───────────┬───────────┘   └──────────┬──────────┘  │
│              │                           │                          │             │
│              └───────────────────────────┼──────────────────────────┘             │
│                                          │                                        │
│                                ┌─────────▼─────────┐                              │
│                                │ IMT5Port Adapter  │                              │
│                                └─────────┬─────────┘                              │
└──────────────────────────────────────────┼────────────────────────────────────────┘
                                           │ Win32 C++ IPC / Encrypted RPC
                                           v
┌───────────────────────────────────────────────────────────────────────────────────┐
│                              METATRADER 5 TERMINAL                                │
│                                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────────────┐  │
│  │   Broker Direct Execution Path (LMAX / IC Markets / Pepperstone / FXCM)     │  │
│  └─────────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────────┘

NexusTradingForexBot/
├── .env.example                               # قالب متغیرهای محیطی
├── .gitignore                                 # لیست فایل‌های نادیده‌گرفته‌شده در گیت
├── docker-compose.yml                         # فایل ارکستراسیون داکر (Core + Postgres)
├── Dockerfile                                 # فایل ساخت ایمیج لینوکس پایتون ۳.۱۱
├── Makefile                                   # دستورات خودکارسازی و بیلد پروژه
├── pyproject.toml                             # تنظیمات پیکربندی پکیج و وابستگی‌ها (PEP 621)
├── README.md                                  # مستندات جامع و سازمانی پروژه
├── requirements.txt                           # لیست کامل وابستگی‌های پایتون
├── NexusTradingForexBot.py                    # نقطه ورود اصلی پروژه (Launcher)
├── NexusTradingForexBot.pyproj                # فایل پروژه پایتون در Visual Studio
├── NexusTradingForexBot.slnx                  # فایل سولوشن XML در Visual Studio 2022
├── configs/
│   ├── base.yaml                              # کانفیگ‌های پایه و پیش‌فرض
│   └── live.yaml                              # کانفیگ اختصاصی حساب زنده (XAUUSD / MT5 / Telegram)
├── docker/
│   ├── entrypoint.sh                          # اسکریپت راه‌اندازی کانتینر
│   └── healthcheck.sh                         # اسکریپت سلامت‌سنجی کانتینر داکر
├── scripts/
│   └── generate_slnx.py                       # اسکریپت پایتون بازتولید فایل Solution
├── src/
│   └── nexus_scalp/
│       ├── __init__.py                        # نسخه پکیج و مشخصات ریشه
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── database/
│       │   │   ├── __init__.py
│       │   │   └── audit_repository.py        # دیتابیس لاگینگ و آودیت معاملات (SQLite / Postgres)
│       │   ├── mt5/
│       │   │   ├── __init__.py
│       │   │   ├── mt5_adapter.py             # آداپتور اصلی MT5 ویندوز (C++ Win32 IPC Driver)
│       │   │   └── remote_gateway.py          # آداپتور ارتباط شبکه‌ای لینوکس به ویندوز (HMAC RPC)
│       │   └── paper/
│       │       ├── __init__.py
│       │       └── paper_adapter.py           # آداپتور شبیه‌ساز معاملات بدون MT5
│       ├── application/
│       │   ├── __init__.py
│       │   └── live_engine.py                 # موتور اصلی اجرای لایو و ارکستراسیون event loop
│       ├── cli/
│       │   ├── __init__.py
│       │   └── main.py                        # رابط خط فرمان (Typer / Rich CLI)
│       ├── configuration/
│       │   ├── __init__.py
│       │   └── config.py                      # پارسر تنظمات پایتون با Pydantic Settings
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── enums.py                       # انوم‌های دامنه (ActionType, OrderType, ExecutionMode)
│       │   └── models.py                      # Value Objectها (TickData, AccountInfo, TradeOrder)
│       ├── execution/
│       │   ├── __init__.py
│       │   └── order_manager.py               # مدیریت پوزیشن‌ها، Hold Score، Trailing Stop و تله‌متری
│       ├── features/
│       │   ├── __init__.py
│       │   └── scalp_features.py              # محاسبه ویژگی‌های ایچیموکو، ICT، ChoCh و عمق قیمت
│       ├── labeling/
│       │   ├── __init__.py
│       │   └── triple_barrier.py              # الگوریتم برچسب‌گذاری Triple-Barrier با کسر هزینه
│       ├── market_data/
│       │   ├── __init__.py
│       │   ├── bar_aggregator.py              # تجمیع‌کننده تیک‌ها به کندل‌های OHLC M1
│       │   └── tick_storage.py                # ذخیره‌سازی داده‌های Parquet
│       ├── models/
│       │   ├── __init__.py
│       │   └── scalp_net.py                   # شبکه عصبی PyTorch با ۳ لایه TCN و Self-Attention (v2)
│       ├── observability/
│       │   ├── __init__.py
│       │   ├── logging.py                     # سیستم لاگینگ ساختاریافته structlog
│       │   └── telegram_notifier.py           # نوتیفایر تلگرام غیربلاک‌کننده با پشتیبانی از Thread Reply
│       ├── ports/
│       │   ├── __init__.py
│       │   ├── gateway_port.py                # قرارداد انتزاعی گیت‌وی
│       │   └── mt5_port.py                    # قرارداد انتزاعی اصلی MT5
│       ├── risk/
│       │   ├── __init__.py
│       │   └── risk_engine.py                 # موتور مدیریت ریسک پویا (حجم‌سنجی مارجین/لوریج)
│       └── signals/
│           ├── __init__.py
│           └── policy.py                      # استراتژی صبورانه ثبت سفارشات Limit/Stop و لایو اسکالپر
└── tests/
    └── unit/
        ├── test_bar_aggregator.py             # تست‌های واحد تجمیع‌کننده کندل
        ├── test_domain_models.py              # تست‌های اینواریانت‌ها و دامنه
        ├── test_logging.py                    # تست‌های لاگینگ
        ├── test_mt5_adapter.py                # تست‌های آداپتور
        ├── test_risk_engine.py                # تست‌های حجم‌سنجی مدیریت ریسک
        └── test_scalp_features.py             # تست‌های استخراج ویژگی‌ها


        🚀 Quick Start Guide
Prerequisites
Windows 10/11
Python 3.11+
MetaTrader 5 Terminal (Logged into Demo or Real account with Allow Algo Trading enabled)
Installation
Clone Repository & Setup Virtual Environment:
code
Powershell
cd C:\Users\Capsizer\source\repos\NexusTradingForexBot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
Install Dependencies:
code
Powershell
pip install -r requirements.txt
Configure Telegram & Risk Parameters:
Edit configs/live.yaml:
code
Yaml
telegram:
  enabled: true
  bot_token: "YOUR_BOT_TOKEN"
  admin_id: "YOUR_CHAT_ID"
Run Pre-Flight Infrastructure Doctor Check:
code
Powershell
python NexusTradingForexBot.py --doctor
Launch Real-Time Live Scalper:
code
Powershell
python NexusTradingForexBot.py
🧪 Testing
Run the unit test suite to verify domain math, feature parity, and risk invariants:
code
Powershell
pytest
🛡️ License & Operational Safety Disclaimer
This software is for quantitative research and automated execution. Live trading carries capital risk. Always verify configuration settings on a demo account before live deployment.
code
Code