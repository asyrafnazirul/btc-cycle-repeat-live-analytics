# btc-cycle-repeat-live-analytics
Real-time 1H Bitcoin 4-year cycle fractal overlay and correlation engine. Built with Python, NiceGUI (FastAPI/WebSockets), and Plotly WebGL with automated multi-CDN API failover
# Bitcoin 4-Year Cycle Live Overlay & Fractal Correlation Engine (1H)

An event-driven financial time-series analytics and forecasting web application that maps historical 4-year Bitcoin market cycles (**Jan 2022 – Dec 2025**) directly onto the live macro timeline (**Jan 2026 – Dec 2029**) at a granular **1-hour timeframe**.

Designed with both **Data Engineering** and **DevOps** resilience principles, this application ingests, caches, and aligns over 35,000+ hourly candles while streaming live incremental delta updates via WebSockets without disrupting user viewport state.

---

## Key Architecture & Features

### Data Engineering & Quantitative Analytics
* **High-Density 1H Time-Series Alignment:** Processes and shifts 35,000+ historical 1-hour OHLCV data points forward by exactly 4 calendar years (`pd.DateOffset(years=4)`), preserving leap-year hourly parity (2024 $\rightarrow$ 2028).
* **Dual-Mode Fractal Scaling:**
  * **Macro Cycle Index:** Normalizes historical percentage returns to the January 1, 2026 opening price.
  * **Live Price Anchor:** Dynamically scales the historical fractal curve to intersect the current live hourly candle for immediate short-term trajectory projection.
* **Forward 24H & 7D Target Engine:** Automatically computes projected price targets and percentage deltas +24 hours and +168 hours ahead based on the anchored fractal.
* **Live Rolling Pearson Correlation & Lead/Lag Optimizer:** Computes real-time 30-Day Rolling (720h) and YTD Pearson correlation coefficients (`r`), complete with an interactive **$\pm$336-hour ($\pm$14-day) Lead/Lag Offset Slider** to detect market front-running or cycle lag.

### DevOps & System Reliability
* **Automated Multi-Provider API Failover:** Automatically probes and routes requests across public market data CDNs (`Binance Vision CloudFront CDN` $\rightarrow$ `Bitstamp REST API`) to bypass regional ISP blocks and prevent single-point-of-failure downtime.
* **Self-Healing Local Cold Storage Cache:** Validates local CSV integrity on startup—automatically recovering and re-paginating corrupted or incomplete historical datasets while avoiding redundant network calls on subsequent boots.
* **Incremental Delta Synchronization:** Rather than re-fetching the entire dataset on every tick, an asynchronous background worker (`asyncio` / `run.io_bound`) queries only the trailing 2-hour delta window every 60 seconds, keeping API bandwidth and memory overhead minimal.
* **Zero-Flicker WebGL Rendering:** Uses Plotly `Scattergl` (WebGL hardware acceleration) paired with `uirevision` state locking over NiceGUI/FastAPI WebSockets so live 60-second updates never reset the user's active zoom or pan coordinates.

---

## Tech Stack
* **Backend & Async Runtime:** Python 3.11+, FastAPI, Uvicorn, AsyncIO (`NiceGUI`)
* **Data Processing & Math:** Pandas (Vectorized time-series manipulation, sliding-window Pearson correlation)
* **Visualization:** Plotly Graph Objects (`Scattergl` WebGL)
* **Data Ingestion:** Requests with persistent HTTP session pooling and rate-limit throttling

---

## Quick Start

### 1. Clone the Repository
```bash
git clone [https://github.com/YOUR_GITHUB_USERNAME/btc-fractal-cycle-engine.git](https://github.com/YOUR_GITHUB_USERNAME/btc-fractal-cycle-engine.git)
cd btc-fractal-cycle-engine
